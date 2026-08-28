"""
app/services/vector_service.py

Self-hosted semantic search over listing descriptions — Vertex's embedding
model to generate vectors, a plain SQLite table (app/data/listings_vec.db)
to store them, brute-force cosine similarity in Python to search them.

Deliberately NOT backed by a managed vector database (Vertex AI Vector
Search / Matching Engine). A deployed Vector Search index endpoint bills
continuously by node-hour regardless of query volume — a real example
found while evaluating this: ~$547.50/month for a 10k-record deployment,
with no pay-as-you-go option. At this app's scale (hundreds of listings,
used interactively for learning/comparison, not production query volume),
that always-on cost buys nothing a brute-force scan doesn't already give
for free — a full similarity scan over hundreds of rows is sub-millisecond
work, proven directly against this same listings data before writing this
file. "Brute-force" only becomes a real tradeoff at a scale (millions of
vectors) this project doesn't have.

Not wired into score_batch/matching_service.py at all — this powers a
separate, standalone search mode (see app/routers/vector_search.py), not
a pre-filter stage inside the existing AI-scored modes.
"""

import array
import math
import sqlite3
from pathlib import Path

from app.config import settings

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
VEC_DB_PATH = DATA_DIR / "listings_vec.db"
EMBEDDING_MODEL = "text-embedding-005"


def _client():
    from google import genai
    if not settings.GCP_PROJECT_ID:
        raise RuntimeError(
            "GCP_PROJECT_ID is not set in .env — required for vector search, "
            "which always uses Vertex's embedding model regardless of AI_PROVIDER."
        )
    return genai.Client(vertexai=True, project=settings.GCP_PROJECT_ID, location=settings.GCP_REGION)


def embed_text(text: str, client=None) -> list[float]:
    client = client or _client()
    r = client.models.embed_content(model=EMBEDDING_MODEL, contents=text)
    return r.embeddings[0].values


def _cos_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS listing_embeddings (
            mls_id      TEXT NOT NULL,
            data_source TEXT NOT NULL,
            embedding   BLOB NOT NULL,
            model       TEXT NOT NULL,
            PRIMARY KEY (mls_id, data_source)
        )
    """)


def index_listings(listings: list[dict], data_source: str, on_progress=None) -> int:
    """Embeds each listing's description and (re)writes its row in
    listing_embeddings, keyed by (mls_id, data_source) so realistic/generated
    can coexist in one file without colliding. Re-running for the same
    data_source overwrites existing rows (INSERT OR REPLACE) rather than
    accumulating duplicates or requiring a manual wipe first.

    on_progress(i, total, listing), if given, is called after each listing
    is embedded — used by the seed script to print progress without this
    function itself knowing anything about printing."""
    client = _client()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(VEC_DB_PATH)
    _ensure_table(conn)

    for i, listing in enumerate(listings, start=1):
        vec = embed_text(listing["description"], client=client)
        blob = array.array("f", vec).tobytes()
        conn.execute(
            "INSERT OR REPLACE INTO listing_embeddings (mls_id, data_source, embedding, model) VALUES (?, ?, ?, ?)",
            (str(listing["mls_id"]), data_source, blob, EMBEDDING_MODEL),
        )
        if on_progress:
            on_progress(i, len(listings), listing)

    conn.commit()
    conn.close()
    return len(listings)


def semantic_search(query: str, listings: list[dict], data_source: str, top_k: int = 20) -> list[dict]:
    """Embeds `query`, brute-force cosine-similarity-ranks it against
    whichever of `listings` have a stored embedding for this data_source,
    returns the top_k as listing dicts with a `similarity` field added
    (0-1 float), best first. Listings with no stored embedding yet
    (index_listings never run, or run for a different data_source) are
    silently skipped rather than erroring — same "don't crash on missing
    data" philosophy as _compute_deterministic_scores' fallback path."""
    if not VEC_DB_PATH.exists():
        raise RuntimeError(
            f"{VEC_DB_PATH.name} doesn't exist yet — run "
            f"`python scripts/build_listing_embeddings.py --data-source {data_source}` first."
        )

    q_vec = embed_text(query)

    conn = sqlite3.connect(VEC_DB_PATH)
    _ensure_table(conn)
    ids = [str(l["mls_id"]) for l in listings]
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT mls_id, embedding FROM listing_embeddings WHERE data_source = ? AND mls_id IN ({placeholders})",
        (data_source, *ids),
    ).fetchall() if ids else []
    conn.close()

    embeddings_by_id = {}
    for mls_id, blob in rows:
        a = array.array("f")
        a.frombytes(blob)
        embeddings_by_id[mls_id] = list(a)

    scored = []
    for listing in listings:
        emb = embeddings_by_id.get(str(listing["mls_id"]))
        if emb is None:
            continue
        scored.append({**listing, "similarity": _cos_sim(q_vec, emb)})

    scored.sort(key=lambda l: l["similarity"], reverse=True)
    return scored[:top_k]
