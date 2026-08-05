"""
scripts/run_cli.py

Standalone command-line demo of the full pipeline — no API server needed.
Useful for quick testing without running uvicorn.

Run from the project root:
    python scripts/run_cli.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.listings_service import HardFilters, fetch_listings, normalize_listing
from app.services.matching_service import rank_listings


def run(hard_filters: HardFilters, user_preferences: str):
    print("Fetching candidate listings...")
    raw = fetch_listings(hard_filters)
    listings = [normalize_listing(r) for r in raw]
    print(f"{len(listings)} listings passed hard filters.\n")

    print("Running AI semantic matching against your preferences...\n")
    ranked = rank_listings(user_preferences, listings)

    print(f"{len(ranked)} listings matched what you're actually looking for:\n")
    for l in ranked:
        print(f"[{l['match_score']}] {l['address']} — ${l['price']:,}")
        print(f"    {l['beds']} bd / {l['baths']} ba / {l['sqft']} sqft")
        print(f"    Why: {l['match_reason']}\n")


if __name__ == "__main__":
    # No hardcoded city filter — sample/realistic/generated are Redwood-City-only
    # anyway, and hardcoding it here would silently zero out results against
    # DATA_SOURCE=live, whose actual city has changed before (see README).
    filters = HardFilters()
    preferences = (
        "I want a quiet street, an updated kitchen, and a spare room "
        "I can use as a home office. I don't want to be on a busy road "
        "or right next to a school. A yard for a dog would be a plus."
    )
    run(filters, preferences)
