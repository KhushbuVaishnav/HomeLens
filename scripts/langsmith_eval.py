"""
scripts/langsmith_eval.py

Uploads scripts/golden_dataset.py's hand-verified ground truth to
LangSmith as a real Dataset, then runs score_batch() against it via
LangSmith's evaluate() API — a SEPARATE, hosted view of the same
accuracy signal scripts/eval_dashboard.py already renders locally.
Deliberately does not touch eval_dashboard.py / eval_dashboard.html at
all — this is an additional dashboard, not a replacement. Same
underlying ground truth either way (both import from golden_dataset.py),
so the two should never disagree about what "correct" means, only about
where you go to look at the result.

One example per (dimension, listing) case — the natural granularity for
LangSmith's dataset/example model — rather than the ceil(N/BATCH_SIZE)
batched calls eval_dashboard.py uses for API economy. That means this
costs MORE real calls than eval_dashboard.py for the same coverage: one
scoring call per listing instead of one call per up-to-BATCH_SIZE-listing
batch. Exactly 173 calls for the full golden dataset on one provider —
every graded case across every dimension + combo (same 173 total
eval_dashboard.py reports as "graded cases", just one real API call per
row here instead of shared batches) — see README before running against
multiple providers back to back.

Requires a REAL LANGSMITH_API_KEY in .env (from smith.langchain.com) —
this talks to LangSmith's dataset/evaluate API directly, which is a
separate thing from the @traceable tracing already wired into
matching_service.py (LANGSMITH_TRACING only affects that, not this).

Run:
    python scripts/langsmith_eval.py                     # AI_PROVIDER only, uploads dataset once
    python scripts/langsmith_eval.py --provider openai
    python scripts/langsmith_eval.py --refresh-dataset    # re-upload examples after editing golden_dataset.py
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `import app.*` works when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))  # so sibling scripts import as plain modules below

from langsmith import Client, evaluate

from app.config import settings, VALID_AI_PROVIDERS
from app.services.listings_service import HardFilters, fetch_listings, normalize_listing
from app.services.matching_service import score_batch

import golden_dataset as gd
from llm_judge import _missing_credentials, _model_for_provider

DATASET_NAME = "homelens-golden-dataset"


def _example(query: str, mls_id: int, listings_by_id: dict, expected_score: int, dimension: str, case: str) -> dict:
    return {
        "inputs": {"query": query, "listing": listings_by_id[mls_id]},
        "outputs": {"expected_score": expected_score},
        "metadata": {"mls_id": mls_id, "dimension": dimension, "case": case},
    }


def _build_examples(listings_by_id: dict) -> list[dict]:
    """Flattens every GRADED case across golden_dataset.py's
    BINARY_DIMENSIONS, AMBIGUOUS_DIMENSIONS (TRUE/FALSE subset only —
    listings in a dimension's `ambiguous` set have no defensible expected
    answer, so they're left out here the same way they're excluded from
    accuracy stats in eval_dashboard.py's _dimension_stats), and
    COMBINED_CASES into one example per (dimension, listing) pair."""
    examples = []

    for dim in gd.BINARY_DIMENSIONS:
        for mls_id in sorted(dim["positive"]):
            examples.append(_example(dim["query"], mls_id, listings_by_id, 100, dim["key"], "positive"))
        for mls_id in sorted(dim["negative"]):
            examples.append(_example(dim["query"], mls_id, listings_by_id, 0, dim["key"], "negative"))

    for dim in gd.AMBIGUOUS_DIMENSIONS:
        for mls_id in sorted(dim["positive"]):
            examples.append(_example(dim["query"], mls_id, listings_by_id, 100, dim["key"], "positive"))
        for mls_id in sorted(dim["negative"]):
            examples.append(_example(dim["query"], mls_id, listings_by_id, 0, dim["key"], "negative"))
        # dim["ambiguous"] listings intentionally excluded — same reasoning
        # as everywhere else in this project: grading against a guess isn't
        # a real accuracy signal.

    for combo in gd.COMBINED_CASES:
        for c in combo["cases"]:
            examples.append(_example(combo["query"], c["mls_id"], listings_by_id, c["expected_score"], combo["key"], "combined"))

    return examples


def _ensure_dataset(client: Client, examples: list[dict], refresh: bool) -> str:
    exists = client.has_dataset(dataset_name=DATASET_NAME)
    if exists and refresh:
        print(f"--refresh-dataset: deleting and recreating '{DATASET_NAME}'...")
        client.delete_dataset(dataset_name=DATASET_NAME)
        exists = False

    if not exists:
        client.create_dataset(
            DATASET_NAME,
            description=(
                "HomeLens golden dataset — hand-verified ground truth from "
                "scripts/golden_dataset.py. One example per (dimension, listing) "
                "case; run against app/data/realistic_listings.json's 14 fixed listings."
            ),
        )
        client.create_examples(dataset_name=DATASET_NAME, examples=examples)
        print(f"Created dataset '{DATASET_NAME}' with {len(examples)} examples.")
    else:
        print(
            f"Dataset '{DATASET_NAME}' already exists in your LangSmith project — reusing it as-is. "
            f"Pass --refresh-dataset to re-upload after changing golden_dataset.py."
        )
    return DATASET_NAME


def _make_target(provider: str):
    """evaluate() calls this once per example, passing that example's
    `inputs` dict — see langsmith.evaluation._runner's _get_target_args,
    which introspects this function's own parameter name ("inputs") to
    know what to pass. Deliberately a batch of ONE listing per call, not
    BATCH_SIZE — see module docstring for why that's the real cost
    tradeoff of using LangSmith's per-example model here."""
    def target(inputs: dict) -> dict:
        result = score_batch(inputs["query"], [inputs["listing"]], ai_provider=provider)[0]
        return {"score": result["score"], "reason": result["reason"]}
    return target


def exact_match(outputs: dict, reference_outputs: dict) -> dict:
    """The evaluator — LangSmith introspects this function's parameter
    names ("outputs", "reference_outputs") the same way it does for the
    target function above. Same pass/fail definition eval_dashboard.py
    uses (actual score must equal the golden dataset's expected score
    exactly, including the 100/50/0-style partial-credit combo cases) —
    same ground truth, same grading rule, just rendered in LangSmith's UI
    instead of the local HTML."""
    correct = outputs.get("score") == reference_outputs.get("expected_score")
    return {"key": "exact_match", "score": 1 if correct else 0}


def main():
    parser = argparse.ArgumentParser(description="Uploads golden_dataset.py to LangSmith and evaluates score_batch() against it.")
    parser.add_argument(
        "--provider", choices=VALID_AI_PROVIDERS, default=None,
        help="Which provider to evaluate. Defaults to AI_PROVIDER in .env.",
    )
    parser.add_argument(
        "--refresh-dataset", action="store_true",
        help="Delete and re-upload the LangSmith dataset first — use after editing golden_dataset.py. "
             "Without this, an existing dataset with the same name is reused as-is.",
    )
    args = parser.parse_args()

    if not os.environ.get("LANGSMITH_API_KEY"):
        print(
            "LANGSMITH_API_KEY is not set in .env — required to use LangSmith. Sign up at "
            "smith.langchain.com, create an API key, and uncomment/fill in the LANGSMITH_* lines "
            "already in .env (see .env.example)."
        )
        sys.exit(1)

    provider = args.provider or settings.AI_PROVIDER
    if provider not in VALID_AI_PROVIDERS:
        print(f"--provider/AI_PROVIDER must be one of {VALID_AI_PROVIDERS}, got '{provider}'")
        sys.exit(1)
    missing = _missing_credentials(provider)
    if missing:
        print(missing)
        sys.exit(1)

    client = Client()

    listings = [normalize_listing(r) for r in fetch_listings(HardFilters(cities=["Redwood City"]), data_source="realistic")]
    listings_by_id = {l["mls_id"]: l for l in listings}
    examples = _build_examples(listings_by_id)

    dataset_name = _ensure_dataset(client, examples, args.refresh_dataset)

    model = _model_for_provider(provider)
    print(f"\nEvaluating {provider} ({model}) against {len(examples)} graded examples — "
          f"{len(examples)} real scoring calls, one per listing per case...\n")

    evaluate(
        _make_target(provider),
        data=dataset_name,
        evaluators=[exact_match],
        experiment_prefix=f"homelens-{provider}",
        max_concurrency=settings.MAX_CONCURRENT_BATCHES,
        client=client,
    )

    print("\nDone — LangSmith prints a direct link to the experiment results above; "
          "open it to see the per-example table, pass/fail, and reasons in your LangSmith project.")


if __name__ == "__main__":
    main()
