# HomeLens

A real-estate listing search POC. Search single-family/condo listings with
structured filters (price, beds, schools, HOA, accessibility), then have an
LLM re-rank results by reading each listing's actual description against a
buyer's freeform preferences — plus a fourth, experimental mode that ranks
by embedding similarity instead, with no LLM call at all, and a fifth mode
where an agent picks whichever of the other three fits the query best.

FastAPI backend, dependency-free React frontend (React + Babel via CDN, no
build step), SQLite for structured data. Three interchangeable AI providers
(Anthropic, OpenAI, Vertex/Gemini) behind one switch.

## Search modes

| Mode | Filters | AI/embedding call | Endpoint |
|---|---|---|---|
| **Traditional** | Hard filters only | None | `POST /listings` |
| **Filters + AI** | Hard filters, then AI re-ranks the result by preferences | LLM, batched | `POST /match` |
| **AI-only** | None — preferences drive everything | LLM, batched | `POST /match` (filters nulled) |
| **Vector search** *(experimental)* | None | Embedding similarity, no LLM | `POST /vector-search` |
| **Smart search** *(agent-routed)* | Optional, passed through unchanged | One small classify call, then dispatches to one of the 3 rows above | `POST /smart-search/classify`, then whichever endpoint it names |

Filters+AI and AI-only run as a background job with progress polling (large
datasets need many batched LLM calls); Traditional and Vector search are
single synchronous requests. Smart search is two calls: the classify step,
then a normal call into whichever mode above the classify step picked.

## Architecture

```
app/
├── main.py                    FastAPI app assembly
├── config.py                  All settings, driven by .env
├── models.py                  Pydantic request/response schemas
├── routers/                   HTTP only — parse request, call a service, shape response
│   ├── listings.py               POST /listings
│   ├── match.py                  POST /match, /match/{job_id}
│   ├── vector_search.py          POST /vector-search
│   └── smart_search.py           POST /smart-search/classify
├── services/                  Actual logic — no HTTP awareness, callable from a script or test
│   ├── listings_service.py       Fetch + filter, all data sources
│   ├── matching_service.py       AI scoring + query classification — Anthropic/OpenAI/Vertex behind one switch
│   ├── schools_service.py        School ratings lookup (SQLite-backed)
│   ├── vector_service.py         Embedding + brute-force cosine similarity search
│   └── router_service.py         Smart search's routing decision — pure logic, no I/O
└── data/                       Datasets, schools DB, listing embeddings
```

```
frontend/          React UI (CDN React + Babel, no build step)
scripts/            CLI tools — see "Scripts" below
docs/architecture.md   Full use-case/logical/sequence/security diagrams
diagrams/           Source .mmd files for the above
```

Routers stay thin on purpose: `scripts/run_cli.py` and `scripts/analyze_scores.py`
call the same services directly, with no API server involved.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
python scripts/seed_schools_db.py
```

Edit `.env`:
- `AI_PROVIDER` — `anthropic` (default), `openai`, or `vertex`
- The matching API key (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`), or for
  `vertex`, `GCP_PROJECT_ID` plus local Application Default Credentials
  (`gcloud auth application-default login`) — no static key for Vertex
- `DATA_SOURCE` — `generated` (large synthetic set) is a good default

See `.env.example` for every setting, with inline notes on what each does.

## Running

```bash
uvicorn app.main:app --reload        # from the project root
```
API docs: **http://127.0.0.1:8000/docs**

```bash
cd frontend && python3 -m http.server 5500
```
Frontend: **http://127.0.0.1:5500** — calls `http://127.0.0.1:8000` by
default; change `API_BASE` in `frontend/app.jsx` if the API runs elsewhere.

The frontend's "Source" and "Matched by" dropdowns override `DATA_SOURCE`/
`AI_PROVIDER` per-search, without touching `.env` or requiring a restart.

## Data sources

- **`live`** — real SimplyRETS sandbox API. Small, third-party, and not
  under our control — listing count and city can change without notice
  (observed: Houston, not Redwood City — clear the City filter when using
  this source). Its `remarks` field is boilerplate, not useful for testing
  match quality.
- **`realistic`** — 14 hand-written Redwood City listings with varied,
  realistic descriptions. Small and fixed by design — this is the dataset
  `verify_test_cases.py`'s AI accuracy test and `golden_dataset.py`'s ground
  truth are both built on.
- **`generated`** — 500+ synthetic listings (`scripts/generate_listings.py N`
  to regenerate at any size). No fixed random seed — every regeneration
  reshuffles everything, so don't hardcode expected counts against it.

At `generated` scale, `matching_service.py` sends `ceil(listings/BATCH_SIZE)`
batched calls to the AI provider per search — use hard filters to narrow
the pool first, or "Browse all (skip AI)" in the frontend for zero AI cost.

## Vector search (experimental)

A standalone 4th mode: embeds listing descriptions and the buyer's query
with Vertex's `text-embedding-005`, ranks by brute-force cosine similarity
in Python, and makes no LLM call. Self-hosted deliberately — a managed
Vertex AI Vector Search endpoint bills continuously by node-hour regardless
of query volume, which doesn't pay off at this app's scale (hundreds of
listings; a full brute-force scan is sub-millisecond).

Build the index once per dataset, ahead of time (not automatic on startup):
```bash
python scripts/build_listing_embeddings.py --data-source realistic
python scripts/build_listing_embeddings.py --data-source generated
```
Stores vectors in `app/data/listings_vec.db`. Only `realistic`/`generated`
are supported — `live` is third-party data, not ours to pre-index.

**Known limitations** (embedding similarity, not a bug in this app):
negation ("not a ranch-style home") is largely invisible to embeddings,
since a negated sentence shares nearly all its vocabulary with its
positive form; compound, multi-clause requirements aren't decomposed into
per-clause judgments the way an LLM does; numeric thresholds ("8,000+
sqft") aren't represented at all. These are well-documented limitations of
dense embedding retrieval generally, not specific to this dataset — see
`scripts/vector_eval_dashboard.py` for measured accuracy against ground
truth, and `docs/architecture.md` §6 for the full design writeup.

## Smart search (agent-routed)

A 5th mode: give filters and/or freeform text, no rules enforced, and the
app decides which of the other 3 modes actually handles the query —
Traditional, Vector search, or AI-scored matching (Filters+AI/AI-only,
whichever filters happen to be present) — then runs it and shows which
one it picked and why. A deliberately simplified deep-agent pattern: it
keeps an explicit plan (a real decomposition of the query into
requirements) and a single reflect-and-revise step, while skipping
sub-agent delegation and a scratchpad/filesystem — a single search fits
in one context window, so that machinery would solve a problem this app
doesn't have. See `docs/architecture.md` §8 for the full design rationale.

**How it decides:**
1. If there's no freeform text, route to **Traditional** — nothing to
   reason about.
2. Otherwise, one small LLM call (`POST /smart-search/classify`, no
   listings payload) decomposes the text into requirement clauses, each
   tagged `negated: true/false` — the same decomposition rule scoring
   already uses. This is the "plan" step.
3. `app/services/router_service.py`'s `decide_route()` — plain Python, not
   the LLM — turns that into a decision: exactly one requirement, not
   negated, routes to **Vector search**; multiple requirements and/or any
   negation routes to **AI-scored matching**, since those are exactly the
   cases vector search is known to handle poorly (see above).
4. **Reflect once**: if Vector search comes back with zero matches, it
   escalates automatically to AI-scored matching and re-runs, rather than
   showing "nothing matched" for what may just be a misrouted query.
5. If the classify call itself fails, it falls back directly to AI-scored
   matching rather than failing the search over a planning-step error.

The routing decision is always shown, not hidden — a badge above the
results states which mode ran and why (e.g. "Routed to Vector search — 1
requirement detected, no negation"), and the backend logs the same
decision server-side. Filters, if given, pass through unchanged to
whichever mode gets picked — nothing about the other 4 modes changes;
this only adds a classification step in front of them.

## Schools

`app/data/schools.json` holds entirely fictional school names and ratings
(synthetic test data only). The app queries `app/data/schools.db`, a SQLite
build of that JSON, rather than parsing it per-request:
```bash
python scripts/seed_schools_db.py   # run once, and again after editing schools.json
```
`schools.db` is read-only at runtime and safe to commit/redeploy on hosts
with an ephemeral filesystem.

## Scripts

| Script | Purpose |
|---|---|
| `generate_listings.py` | Regenerate the synthetic `generated` dataset |
| `seed_schools_db.py` | Build `schools.db` from `schools.json` |
| `build_listing_embeddings.py` | Build the vector-search index for a data source |
| `run_cli.py` | Run the matching pipeline standalone, no API server |
| `analyze_scores.py` | Score distribution for a query — for tuning `SCORE_THRESHOLD` |
| `verify_test_cases.py` | Dataset invariants + keyword regression + (`--with-ai`) real accuracy test |
| `llm_judge.py` | Second AI provider reviews the first's verdicts — triage tool, not ground truth |
| `eval_dashboard.py` | Local HTML dashboard scored against hand-verified ground truth (`golden_dataset.py`) |
| `vector_eval_dashboard.py` | Same ground truth, graded for the vector-search mode |
| `langsmith_eval.py` | Same ground truth, run as a hosted LangSmith evaluation |

Each script's own `--help` and inline comments cover its flags; the
sections below cover the ones with real setup or cost implications.

### Ground truth: `golden_dataset.py`

Single source of truth for "correct" answers, hand-verified against
`realistic_listings.json`'s actual text. `verify_test_cases.py --with-ai`,
`eval_dashboard.py`, `vector_eval_dashboard.py`, and `langsmith_eval.py` all
import from it, so they can't drift out of sync with each other. Covers 8
binary dimensions (quiet street, home office, walkable to Caltrain,
single-story, HOA condo, newer construction, large lot, not-ranch-style),
2 dimensions with a deliberately-ungraded ambiguous slice (school quality,
recently renovated), and combined 2–5-requirement cases.

### LLM-as-judge: `llm_judge.py`

```bash
python scripts/llm_judge.py                            # default: 14 listings, 3 queries
python scripts/llm_judge.py --full-sweep --sample 50    # sample of DATA_SOURCE
python scripts/llm_judge.py --scorer openai --judge vertex
python scripts/llm_judge.py --review                    # interactively record who was right
```
By default the provider you're *not* scoring with reviews the scorer's
verdicts (`--scorer`/`--judge` override either side). This is triage, not
ground truth — a `[DISAGREE]` flags a verdict worth a human look; agreement
isn't proof of correctness (`--spot-check-agrees N` samples agreements too).
Writes `scripts/llm_judge_output.csv` with every listing/query/verdict.

### Ground-truth dashboards

```bash
python scripts/eval_dashboard.py --compare-providers      # AI-scored modes, all 3 providers
python scripts/vector_eval_dashboard.py --data-source realistic
python scripts/langsmith_eval.py                          # same ground truth, hosted on LangSmith
```
`eval_dashboard.py`/`vector_eval_dashboard.py` render standalone, offline
HTML reports. `langsmith_eval.py` uploads `golden_dataset.py` to LangSmith
as a Dataset and runs `evaluate()` against it — one scoring call per
listing (173 total), vs. the local dashboards' shared batches.

### Tuning `SCORE_THRESHOLD`

```bash
python scripts/analyze_scores.py "your test preferences"
```
Prints the real score distribution for a query with the threshold
disabled — set `SCORE_THRESHOLD` from where a real gap appears in your
data, not a guess.

## LangSmith tracing

Optional, off by default. `matching_service.py`'s scoring functions are
`@traceable` — a no-op unless `LANGSMITH_TRACING=true` and
`LANGSMITH_API_KEY` are set in `.env`. Once enabled, every real scoring
call (from the app, or any script) appears in your LangSmith project with
the actual prompt, response, token/cache usage, and latency. Independent
of `langsmith_eval.py` above, which talks to LangSmith's dataset/evaluate
API directly regardless of whether tracing is on.

## Prompt caching

| Provider | Mechanism | Status |
|---|---|---|
| Anthropic | Opt-in via `cache_control`; needs the tagged block ≥ ~1024 tokens (Sonnet/Opus) | Tagged, but currently a no-op — `SYSTEM_PROMPT` is ~700 tokens, under the threshold |
| OpenAI | Automatic server-side prefix caching, ≥1024-token prompts | Working, no code required |
| Vertex (Gemini) | Automatic ("implicit caching"), same mechanic | Working, no code required |

Every provider logs real `cache read`/`cache write` token counts per call
in `matching_service.py` — check those rather than assuming behavior from
the code alone. Anthropic caching activates automatically if `SYSTEM_PROMPT`
grows past ~1024 tokens or the model changes; no code change needed.

## Deployment

Backend: `Dockerfile` builds a standalone image (`uvicorn app.main:app`,
binds to `$PORT`) — no database server or file writes needed at runtime,
`app/data/*.db`/`*.json` are pre-built and committed. Frontend: a separate
static site (`frontend/Dockerfile`), pointed at the backend via `API_BASE`;
the backend's `CORS_ALLOW_ORIGINS` must list the frontend's real origin in
production (`*` is fine for local dev only). Vertex/Gemini authenticates
via the deployed service's own IAM identity (grant it the "Vertex AI User"
role) — no API key to manage for that provider in production.

## Moving to real MLS data

Swap `DATA_SOURCE=live` for a production feed: SimplyRETS production,
Bridge Interactive (CoreLogic Trestle), or Spark API all share a similar
RESO-based response shape — `listings_service.py`'s `_fetch_from_simplyrets`
is the only place that would need to change. Zillow/Redfin/Trulia don't
offer general-purpose listing APIs; scraping them violates their terms of
service.
