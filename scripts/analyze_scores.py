"""
scripts/analyze_scores.py

Runs every listing through the AI scorer with SCORE_THRESHOLD effectively
disabled, and prints every score sorted highest to lowest — how you replace
a guessed threshold with one backed by your actual data's score distribution.

Also writes a full CSV (score, mls_id, address, reason — untruncated) next
to this script, since a fixed-width terminal table can't display a full
reason string without cutting it off mid-sentence. Open the CSV in a
spreadsheet for a readable, sortable, filterable view when you're actually
trying to verify individual scores against their reasons.

Run from the project root:
    python scripts/analyze_scores.py "your preferences text here"
"""

import csv
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

    # Terminal table: address column stays fixed-width for scanability, but
    # the reason is printed in full — no truncation. Long reasons will wrap
    # in the terminal (the address/score alignment on the wrapped line
    # won't be perfect), but nothing is ever cut off mid-sentence anymore.
    print(f"{'SCORE':<8}{'ADDRESS':<35}{'REASON'}")
    print("-" * 100)
    for l in ranked:
        addr = (l["address"] or "Unknown")[:33]
        print(f"{l['match_score']:<8}{addr:<35}{l['match_reason']}")

    scores = [l["match_score"] for l in ranked]
    if scores:
        print(f"\nMin: {min(scores)}  Max: {max(scores)}  Avg: {sum(scores)/len(scores):.1f}")
        for threshold in (40, 50, 60, 70):
            print(f"Count above {threshold}: {sum(1 for s in scores if s >= threshold)}")

    # CSV export — the actual tool for visually verifying scores against
    # their reasons. A spreadsheet doesn't truncate, doesn't wrap oddly,
    # and lets you sort/filter/search across all listings at once.
    out_path = Path(__file__).resolve().parent / "analyze_scores_output.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["score", "mls_id", "address", "reason"])
        for l in ranked:
            writer.writerow([l["match_score"], l["mls_id"], l["address"] or "Unknown", l["match_reason"]])
    print(f"\nFull results (untruncated) written to: {out_path}")


if __name__ == "__main__":
    main()
