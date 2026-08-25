"""
scripts/eval_dashboard.py

Renders scripts/eval_dashboard.html — a self-contained, offline-viewable
accuracy report built from the hand-verified golden dataset in
scripts/golden_dataset.py (app/data/realistic_listings.json's 14 fixed
listings). That module is the single source of truth for every expected
answer here — nothing in this script invents or duplicates ground truth.

Unlike llm_judge.py (one model reviewing another — neither is inherently
more correct), this checks against REAL ground truth: every expected
answer was verified by a human actually reading the listing's remarks
text (and, for schools, the real ratings data). This is "is the scorer
actually right", not "do two models agree."

Covers:
  - 8 single-requirement dimensions (golden_dataset.BINARY_DIMENSIONS),
    each with real positive AND negative listings — quiet street, home
    office, walkable to Caltrain, single-story/no-stairs, low-maintenance
    condo/HOA, newer construction, large lot, and a NEGATION case
    ("definitely not a ranch-style home").
  - 2 ambiguous-first dimensions (golden_dataset.AMBIGUOUS_DIMENSIONS) —
    highly-rated schools and recently-renovated kitchen — where a
    meaningful chunk of listings are deliberately left ungraded because
    no defensible single answer exists; shown for transparency (the
    model's real answer, no verdict) rather than silently dropped.
  - 4 combined multi-requirement cases from 2 up to 5 requirements at
    once (golden_dataset.COMBINED_CASES), testing the deterministic
    met/total scoring math together with classification accuracy.

Excluded/ambiguous listings are never hidden — every dimension's card in
the dashboard shows exactly which listings had no ground truth and why,
so the golden dataset is never silently narrowed without saying so.

Run:
    python scripts/eval_dashboard.py                       # AI_PROVIDER only
    python scripts/eval_dashboard.py --provider openai      # one specific provider
    python scripts/eval_dashboard.py --compare-providers    # all 3, side by side (3x the calls)
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `import app.*` works when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))  # so sibling scripts import as plain modules below

from app.config import settings, VALID_AI_PROVIDERS
from app.services.listings_service import HardFilters, fetch_listings, normalize_listing
from app.services.matching_service import score_batch

import golden_dataset as gd
from llm_judge import _missing_credentials, _model_for_provider

OUTPUT_PATH = Path(__file__).resolve().parent / "eval_dashboard.html"


def _fetch_scores(preferences: str, listings: list[dict], provider: str) -> dict:
    """Same batching economy as verify_test_cases.py's own _fetch_all_scores
    (score_batch, not rank_listings — bypasses SCORE_THRESHOLD so listings
    that SHOULD score low are still returned, not silently filtered out),
    but takes an explicit provider instead of always using whatever
    AI_PROVIDER happens to be set to, since this script needs to run
    against a provider that may not be the configured default."""
    scores_by_id = {}
    for i in range(0, len(listings), settings.BATCH_SIZE):
        batch = listings[i:i + settings.BATCH_SIZE]
        for r in score_batch(preferences, batch, ai_provider=provider):
            scores_by_id[int(r["mls_id"])] = r
    return scores_by_id


def _row(mls_id: int, listings_by_id: dict, scores: dict, case: str, expected_label: str, expected_score) -> dict:
    r = scores.get(mls_id)
    actual = r["score"] if r else None
    return {
        "mls_id": mls_id,
        "address": (listings_by_id.get(mls_id) or {}).get("address"),
        "case": case,
        "expected": expected_label,
        "actual_score": actual,
        "pass": (actual == expected_score) if expected_score is not None else None,
        "reason": (r or {}).get("reason", ""),
    }


def _evaluate_binary_dimension(dim: dict, listings: list[dict], listings_by_id: dict, provider: str) -> dict:
    scores = _fetch_scores(dim["query"], listings, provider)
    rows = (
        [_row(mid, listings_by_id, scores, "positive", "MET", 100) for mid in sorted(dim["positive"])]
        + [_row(mid, listings_by_id, scores, "negative", "NOT MET", 0) for mid in sorted(dim["negative"])]
    )
    all_ids = {l["mls_id"] for l in listings}
    excluded = sorted(all_ids - dim["positive"] - dim["negative"])
    return {
        "key": dim["key"], "label": dim["label"], "query": dim["query"], "type": "binary",
        "rows": rows,
        "excluded": [{"mls_id": mid, "address": (listings_by_id.get(mid) or {}).get("address")} for mid in excluded],
    }


def _evaluate_ambiguous_dimension(dim: dict, listings: list[dict], listings_by_id: dict, provider: str) -> dict:
    """Same shape as a binary dimension's rows, plus a third case type
    ("ambiguous") that carries the model's real score but no expected
    value and no pass/fail — those rows are informational only, never
    counted into accuracy stats (see _dimension_stats)."""
    scores = _fetch_scores(dim["query"], listings, provider)
    rows = (
        [_row(mid, listings_by_id, scores, "positive", "MET", 100) for mid in sorted(dim["positive"])]
        + [_row(mid, listings_by_id, scores, "negative", "NOT MET", 0) for mid in sorted(dim["negative"])]
        + [_row(mid, listings_by_id, scores, "ambiguous", "(no ground truth)", None) for mid in sorted(dim["ambiguous"])]
    )
    return {
        "key": dim["key"], "label": dim["label"], "query": dim["query"], "type": "ambiguous",
        "rows": rows,
        "excluded": [],
    }


def _evaluate_combo(combo: dict, listings: list[dict], listings_by_id: dict, provider: str) -> dict:
    scores = _fetch_scores(combo["query"], listings, provider)
    rows = []
    for c in combo["cases"]:
        mls_id = c["mls_id"]
        r = scores.get(mls_id)
        actual = r["score"] if r else None
        rows.append({
            "mls_id": mls_id,
            "address": (listings_by_id.get(mls_id) or {}).get("address"),
            "case": "positive" if c["met"] == c["total"] else ("negative" if c["met"] == 0 else "partial"),
            "expected": f"{c['met']}/{c['total']} met ({c['expected_score']})",
            "actual_score": actual,
            "pass": actual == c["expected_score"],
            "reason": (r or {}).get("reason", ""),
        })
    all_ids = {l["mls_id"] for l in listings}
    covered = {c["mls_id"] for c in combo["cases"]}
    excluded = sorted(all_ids - covered)
    return {
        "key": combo["key"], "label": combo["label"], "query": combo["query"], "type": "combined", "n": combo["n"],
        "rows": rows,
        "excluded": [{"mls_id": mid, "address": (listings_by_id.get(mid) or {}).get("address")} for mid in excluded],
    }


def _dimension_stats(dim_result: dict) -> dict:
    """Ambiguous rows (pass is None) never count toward totals — they're
    displayed but not graded, same principle as an excluded listing,
    just surfaced inline instead of in a separate excluded list."""
    graded = [r for r in dim_result["rows"] if r["pass"] is not None]
    total = len(graded)
    correct = sum(1 for r in graded if r["pass"])
    by_case = {}
    for case in sorted({r["case"] for r in dim_result["rows"]}):
        case_rows = [r for r in dim_result["rows"] if r["case"] == case]
        graded_case_rows = [r for r in case_rows if r["pass"] is not None]
        by_case[case] = {
            "total": len(case_rows),
            "graded": len(graded_case_rows),
            "correct": sum(1 for r in graded_case_rows if r["pass"]),
        }
    return {
        "total": total, "correct": correct,
        "accuracy": (100 * correct / total) if total else None,
        "by_case": by_case,
        "ungraded": len(dim_result["rows"]) - total,
    }


def run_eval(providers: list[str]) -> dict:
    filters = HardFilters(cities=["Redwood City"])
    listings = [normalize_listing(r) for r in fetch_listings(filters, data_source="realistic")]
    listings_by_id = {l["mls_id"]: l for l in listings}
    print(f"Evaluating against {len(listings)} fixed, hand-verified listings (app/data/realistic_listings.json).")

    results_by_provider = {}
    for provider in providers:
        model = _model_for_provider(provider)
        print(f"\n=== {provider} ({model}) ===")
        dims = []

        for dim in gd.BINARY_DIMENSIONS:
            print(f"  scoring: {dim['label']}...")
            dims.append(_evaluate_binary_dimension(dim, listings, listings_by_id, provider))

        for dim in gd.AMBIGUOUS_DIMENSIONS:
            print(f"  scoring (ambiguous-first): {dim['label']}...")
            dims.append(_evaluate_ambiguous_dimension(dim, listings, listings_by_id, provider))

        for combo in gd.COMBINED_CASES:
            print(f"  scoring combo: {combo['label']}...")
            dims.append(_evaluate_combo(combo, listings, listings_by_id, provider))

        for d in dims:
            d["stats"] = _dimension_stats(d)

        overall_total = sum(d["stats"]["total"] for d in dims)
        overall_correct = sum(d["stats"]["correct"] for d in dims)
        overall_ungraded = sum(d["stats"]["ungraded"] for d in dims)
        results_by_provider[provider] = {
            "provider": provider,
            "model": model,
            "dimensions": dims,
            "overall": {
                "total": overall_total, "correct": overall_correct, "ungraded": overall_ungraded,
                "accuracy": (100 * overall_correct / overall_total) if overall_total else None,
            },
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": "app/data/realistic_listings.json (14 hand-verified listings)",
        "listing_count": len(listings),
        "providers": results_by_provider,
    }


def _render_html(data: dict) -> str:
    template_path = Path(__file__).resolve().parent / "eval_dashboard_template.html"
    template = template_path.read_text(encoding="utf-8")
    return template.replace("__EVAL_DATA__", json.dumps(data))


def main():
    parser = argparse.ArgumentParser(description="Renders an HTML accuracy dashboard from the hand-verified golden dataset in scripts/golden_dataset.py.")
    parser.add_argument(
        "--provider", choices=VALID_AI_PROVIDERS, default=None,
        help="Evaluate one specific provider. Defaults to AI_PROVIDER in .env.",
    )
    parser.add_argument(
        "--compare-providers", action="store_true",
        help="Evaluate all three providers side by side instead of just one. 3x the API calls of a "
             "single-provider run — see README for the call-count table.",
    )
    args = parser.parse_args()

    if args.compare_providers and args.provider:
        print("--provider and --compare-providers are mutually exclusive — pick one.")
        sys.exit(1)

    providers = list(VALID_AI_PROVIDERS) if args.compare_providers else [args.provider or settings.AI_PROVIDER]

    missing = {p: _missing_credentials(p) for p in providers}
    missing = {p: m for p, m in missing.items() if m}
    if missing:
        for p, m in missing.items():
            print(m)
        sys.exit(1)

    data = run_eval(providers)
    OUTPUT_PATH.write_text(_render_html(data), encoding="utf-8")

    print(f"\n{'=' * 70}")
    for provider, r in data["providers"].items():
        acc = r["overall"]["accuracy"]
        if acc is None:
            print(f"{provider}: no cases evaluated")
        else:
            print(f"{provider} ({r['model']}): {r['overall']['correct']}/{r['overall']['total']} ({acc:.0f}%) "
                  f"— {r['overall']['ungraded']} ungraded (ambiguous)")
    print(f"\nDashboard written to: {OUTPUT_PATH}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
