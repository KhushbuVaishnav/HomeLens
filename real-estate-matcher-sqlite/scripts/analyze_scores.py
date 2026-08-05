"""
scripts/analyze_scores.py

Runs every listing through the AI scorer with SCORE_THRESHOLD effectively
disabled, and prints every score sorted highest to lowest — how you replace
a guessed threshold with one backed by your actual data's score distribution.

Run from the project root:
    python scripts/analyze_scores.py "your preferences text here"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `import app.*` works when run as a script

from app.config import settings
from app.services.listings_service import HardFilters, fetch_listings, normalize_listing
from app.services import matching_service

settings.SCORE_THRESHOLD = 0  # override for this analysis run — we want to see EVERY score


def main():
    if len(sys.argv) < 2:
        preferences = "quiet street, updated kitchen, home office space"
        print(f"No preferences given, using default: \"{preferences}\"\n")
    else:
        preferences = sys.argv[1]
        print(f"Preferences: \"{preferences}\"\n")

    print(f"Data source: {settings.DATA_SOURCE}\n")

    filters = HardFilters()  # no hardcoded city — see README for why (live source's city isn't fixed)
    raw = fetch_listings(filters)
    listings = [normalize_listing(r) for r in raw]
    print(f"Scoring {len(listings)} listings...\n")

    ranked = matching_service.rank_listings(preferences, listings)

    print(f"{'SCORE':<8}{'ADDRESS':<35}{'REASON'}")
    print("-" * 100)
    for l in ranked:
        addr = (l["address"] or "Unknown")[:33]
        print(f"{l['match_score']:<8}{addr:<35}{l['match_reason'][:60]}")

    scores = [l["match_score"] for l in ranked]
    if scores:
        print(f"\nMin: {min(scores)}  Max: {max(scores)}  Avg: {sum(scores)/len(scores):.1f}")
        for threshold in (40, 50, 60, 70):
            print(f"Count above {threshold}: {sum(1 for s in scores if s >= threshold)}")


if __name__ == "__main__":
    main()
