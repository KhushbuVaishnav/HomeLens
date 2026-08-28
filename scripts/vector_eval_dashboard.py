"""
scripts/vector_eval_dashboard.py

Ground-truth accuracy check for the experimental Vector search mode —
same golden dataset, same hand-verified ground truth as
scripts/eval_dashboard.py (which grades the AI-scored modes), so the two
are directly comparable on the exact same questions. Separate script, not
a flag on eval_dashboard.py, because the grading methodology has to be
genuinely different — see below.

WHY THE GRADING METHOD IS DIFFERENT FROM eval_dashboard.py
------------------------------------------------------------
The AI-scored modes produce a deterministic 0-100 score from a
requirements breakdown (met/total) — there's a natural pass/fail: does
the score match the expected number exactly. Vector search produces one
raw, uncalibrated cosine similarity float per listing — there's no
built-in "MET" threshold to compare against a percentage.

Rather than invent an arbitrary similarity cutoff (which would silently
bake in a guess), this uses a rank-based rule instead: for a dimension
with N golden-TRUE listings, the N listings ranked most similar to the
query are treated as "predicted positive," everyone else as "predicted
negative." Since |predicted positive| == |actual positive| by
construction, precision@N and recall@N are identical here, and the
result is a clean per-listing accuracy number directly comparable to
eval_dashboard.py's, with no threshold to justify or tune.

WHY COMBINED_CASES (2-5 requirement combos) ARE NOT GRADED HERE
------------------------------------------------------------
A single embedding of a 5-clause compound query ("quiet street, a home
office, no stairs, not ranch, large lot") is one holistic similarity
number — it cannot be decomposed back into "3 of 5 requirements met" the
way the AI's per-requirement breakdown can. Grading it against a
combined_score expectation would be comparing two fundamentally
different kinds of output. Only golden_dataset.py's single-requirement
dimensions (BINARY_DIMENSIONS + the graded TRUE/FALSE subset of
AMBIGUOUS_DIMENSIONS) are evaluated.

TWO MODES
------------------------------------------------------------
--data-source realistic (default): real accuracy grading against
    golden_dataset.py's hand-verified ground truth — the only dataset
    that ground truth exists for (its mls_ids are all realistic
    listings, 2001001-2001014; they don't exist in `generated` at all).
--data-source generated --ungraded: no ground truth exists for the
    500-listing generated set, so this runs the same queries and shows
    real ranked results and similarity scores, WITHOUT claiming an
    accuracy number — a smoke/behavior check at scale, not an accuracy
    check. Refuses to run in graded mode against `generated` since that
    would silently compare against nonexistent ground truth.

Run:
    python scripts/vector_eval_dashboard.py                                  # realistic, graded
    python scripts/vector_eval_dashboard.py --data-source generated --ungraded  # generated, ungraded smoke run
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.listings_service import HardFilters, fetch_listings, normalize_listing
from app.services import vector_service

import golden_dataset as gd

OUTPUT_DIR = Path(__file__).resolve().parent


def _output_path(data_source: str, mode: str) -> Path:
    # Separate file per (data_source, mode) — realistic/graded and
    # generated/ungraded are genuinely different reports (one has an
    # accuracy number, one doesn't), and running one must never silently
    # overwrite the other.
    return OUTPUT_DIR / f"vector_eval_dashboard_{data_source}_{mode}.html"

GRADED_DIMENSIONS = gd.BINARY_DIMENSIONS + [
    {"key": d["key"], "label": d["label"], "query": d["query"], "positive": d["positive"], "negative": d["negative"]}
    for d in gd.AMBIGUOUS_DIMENSIONS
]


def _rank_by_similarity(query: str, listings: list[dict], data_source: str) -> list[dict]:
    """semantic_search already sorts best-first; top_k=len(listings) so
    nothing gets truncated — this call wants a full ranking, not a
    shortlist."""
    return vector_service.semantic_search(query, listings, data_source, top_k=len(listings))


def _evaluate_dimension_graded(dim: dict, listings_by_id: dict, data_source: str) -> dict:
    positive, negative = dim["positive"], dim["negative"]
    relevant_ids = positive | negative
    relevant_listings = [listings_by_id[mid] for mid in relevant_ids if mid in listings_by_id]

    ranked = _rank_by_similarity(dim["query"], relevant_listings, data_source)
    n = len(positive)
    predicted_positive_ids = {int(l["mls_id"]) for l in ranked[:n]}

    rows = []
    for rank, l in enumerate(ranked, start=1):
        mls_id = int(l["mls_id"])
        actual_positive = mls_id in positive
        predicted_positive = mls_id in predicted_positive_ids
        rows.append({
            "mls_id": mls_id,
            "address": l.get("address"),
            "rank": rank,
            "similarity": l["similarity"],
            "case": "positive" if actual_positive else "negative",
            "expected": "MET" if actual_positive else "NOT MET",
            "predicted": "MET" if predicted_positive else "NOT MET",
            "pass": actual_positive == predicted_positive,
        })

    all_ids = set(listings_by_id.keys())
    excluded = sorted(all_ids - relevant_ids)
    return {
        "key": dim["key"], "label": dim["label"], "query": dim["query"], "type": "graded",
        "rows": rows,
        "excluded": [{"mls_id": mid, "address": listings_by_id[mid].get("address")} for mid in excluded if mid in listings_by_id],
    }


def _dimension_stats(dim_result: dict) -> dict:
    rows = dim_result["rows"]
    total = len(rows)
    correct = sum(1 for r in rows if r["pass"])
    by_case = {}
    for case in sorted({r["case"] for r in rows}):
        case_rows = [r for r in rows if r["case"] == case]
        by_case[case] = {"total": len(case_rows), "correct": sum(1 for r in case_rows if r["pass"])}
    return {"total": total, "correct": correct, "accuracy": (100 * correct / total) if total else None, "by_case": by_case}


def run_graded_eval(data_source: str) -> dict:
    if data_source != "realistic":
        print(
            f"Refusing to run graded evaluation against data_source='{data_source}' — "
            f"golden_dataset.py's ground truth only exists for 'realistic' (its mls_ids are "
            f"realistic_listings.json's 14 listings, 2001001-2001014; they don't exist in "
            f"'{data_source}' at all). Use --ungraded for a real, non-accuracy-graded run "
            f"against '{data_source}'."
        )
        sys.exit(1)

    listings = [normalize_listing(r) for r in fetch_listings(HardFilters(cities=["Redwood City"]), data_source=data_source)]
    listings_by_id = {l["mls_id"]: l for l in listings}
    print(f"Evaluating vector search against {len(listings)} fixed, hand-verified listings ({data_source}).")

    dims = []
    for dim in GRADED_DIMENSIONS:
        print(f"  ranking: {dim['label']}...")
        dims.append(_evaluate_dimension_graded(dim, listings_by_id, data_source))

    for d in dims:
        d["stats"] = _dimension_stats(d)

    overall_total = sum(d["stats"]["total"] for d in dims)
    overall_correct = sum(d["stats"]["correct"] for d in dims)

    return {
        "mode": "graded",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": f"app/data/realistic_listings.json (14 hand-verified listings) — {data_source}",
        "listing_count": len(listings),
        "dimensions": dims,
        "overall": {"total": overall_total, "correct": overall_correct, "accuracy": (100 * overall_correct / overall_total) if overall_total else None},
        "note": (
            "Rank-based grading: for a dimension with N golden-TRUE listings, the N "
            "listings ranked most similar to the query are treated as 'predicted MET.' "
            "COMBINED_CASES (2-5 requirement combos) are not graded here — a single "
            "compound-query embedding can't be decomposed into a per-requirement met/total "
            "breakdown the way the AI-scored modes' output can."
        ),
    }


def run_ungraded_eval(data_source: str) -> dict:
    listings = [normalize_listing(r) for r in fetch_listings(HardFilters(), data_source=data_source)]
    print(f"Running ungraded vector search against {len(listings)} listings ({data_source}) — no ground truth exists here, real results only, no accuracy claim.")

    dims = []
    for dim in GRADED_DIMENSIONS:
        print(f"  ranking: {dim['label']}...")
        ranked = _rank_by_similarity(dim["query"], listings, data_source)[:10]
        dims.append({
            "key": dim["key"], "label": dim["label"], "query": dim["query"], "type": "ungraded",
            "rows": [
                {"mls_id": l["mls_id"], "address": l.get("address"), "rank": i + 1, "similarity": l["similarity"]}
                for i, l in enumerate(ranked)
            ],
        })

    return {
        "mode": "ungraded",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": f"app/data/{data_source}_listings.json ({len(listings)} listings) — {data_source}",
        "listing_count": len(listings),
        "dimensions": dims,
        "note": (
            f"No ground truth exists for '{data_source}' — golden_dataset.py's labels are "
            f"specific to realistic_listings.json's 14 fixed listings. This shows the top 10 "
            f"real results per query at real scale, not an accuracy grade."
        ),
    }


def _render_html(data: dict) -> str:
    template_name = "vector_eval_dashboard_template.html"
    template = (Path(__file__).resolve().parent / template_name).read_text(encoding="utf-8")
    return template.replace("__EVAL_DATA__", json.dumps(data))


def main():
    parser = argparse.ArgumentParser(description="Evaluate the experimental Vector search mode against golden_dataset.py's ground truth.")
    parser.add_argument("--data-source", choices=("realistic", "generated"), default="realistic")
    parser.add_argument("--ungraded", action="store_true", help="Run without accuracy grading — required for --data-source generated, since no ground truth exists for it.")
    args = parser.parse_args()

    if args.data_source == "generated" and not args.ungraded:
        print("--data-source generated requires --ungraded — see the module docstring for why.")
        sys.exit(1)

    data = run_ungraded_eval(args.data_source) if args.ungraded else run_graded_eval(args.data_source)
    output_path = _output_path(args.data_source, data["mode"])
    output_path.write_text(_render_html(data), encoding="utf-8")

    print(f"\n{'=' * 70}")
    if data["mode"] == "graded":
        acc = data["overall"]["accuracy"]
        print(f"vector search ({args.data_source}): {data['overall']['correct']}/{data['overall']['total']} ({acc:.0f}%)" if acc is not None else "no cases evaluated")
    else:
        print(f"vector search ({args.data_source}): ungraded run complete, {data['listing_count']} listings, top 10 shown per dimension")
    print(f"\nDashboard written to: {output_path}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
