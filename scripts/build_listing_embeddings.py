"""
scripts/build_listing_embeddings.py

Builds/refreshes app/data/listings_vec.db for the standalone Vector search
(experimental) mode — embeds every listing's description with Vertex's
embedding model and stores it via app.services.vector_service.index_listings.

Parallel to scripts/seed_schools_db.py in spirit (a manual, re-run-anytime
build step), but deliberately NOT run automatically on app startup the way
schools.db's structured data is implicitly always fresh — embedding calls
cost real (if tiny) money, so building the index is a conscious, visible
action here, not hidden background spend.

Only realistic/generated make sense to pre-index (fixed or regeneratable
datasets we own); live is SimplyRETS' external sandbox data, not ours to
embed and cache.

Run from the project root:
    python scripts/build_listing_embeddings.py --data-source realistic
    python scripts/build_listing_embeddings.py --data-source generated
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.listings_service import HardFilters, fetch_listings, normalize_listing
from app.services.vector_service import index_listings, VEC_DB_PATH


def main():
    parser = argparse.ArgumentParser(description="Embed listing descriptions into app/data/listings_vec.db for the Vector search mode.")
    parser.add_argument(
        "--data-source", choices=("realistic", "generated"), required=True,
        help="Which fixed dataset to index. 'live' isn't supported — it's external, dynamic data, not ours to pre-index.",
    )
    args = parser.parse_args()

    raw = fetch_listings(HardFilters(), data_source=args.data_source)
    listings = [normalize_listing(r) for r in raw]
    print(f"Embedding {len(listings)} listings from '{args.data_source}'...")

    def on_progress(i, total, listing):
        print(f"  [{i}/{total}] {listing.get('address') or listing['mls_id']}")

    index_listings(listings, args.data_source, on_progress=on_progress)
    print(f"\nDone — {len(listings)} embeddings stored in {VEC_DB_PATH} (data_source='{args.data_source}')")


if __name__ == "__main__":
    main()
