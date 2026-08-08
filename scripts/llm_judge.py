"""
scripts/llm_judge.py

LLM-as-judge: has whichever AI provider you're NOT scoring with review
score_batch()'s real verdicts, using the SAME batching economy scoring
already uses (up to BATCH_SIZE listings per call) — reviewing N listings
costs ceil(N/8) judge calls, not N individual ones. Full per-listing
attribution is preserved in the output either way, same as score_batch
already does for multiple listings sharing one call.

NOT a replacement for verify_test_cases.py's Tier 3 hand-verified ground
truth. A judge model isn't inherently more trustworthy than the model
being judged — if both share the same blind spot, they'll agree
confidently while both being wrong. What this IS good for: triage.
Instead of reading through every listing yourself, you get a shortlist of
exactly which verdicts a second model disagreed with, worth a human look.
A disagreement is a prioritization signal, not proof of an error — you
still make the final call, same as you already do for Tier 3.

Never runs as part of the live app — nothing in app/routers or app/main.py
touches this. Purely a manual dev tool, same category as analyze_scores.py
and verify_test_cases.py.

Cost, in real call counts (see README for the full breakdown):
    Default scope (Option A) — the same 14 fixed, hand-verified listings
    and 3 queries Tier 3 already uses: 6 scoring calls + 6 judge calls =
    12 calls total, all 3 queries combined.

    --full-sweep — judges DATA_SOURCE's actual dataset (500 listings for
    `generated`) instead: real API cost, not something to run by default.
    Combine with --sample to judge a random subset instead of everything.

Run:
    python scripts/llm_judge.py
    python scripts/llm_judge.py --full-sweep --sample 50
    python scripts/llm_judge.py --full-sweep --query "walkable to Caltrain, quiet street"
"""

import argparse
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `import app.*` works when run as a script

from app.config import settings, VALID_AI_PROVIDERS
from app.services.listings_service import HardFilters, fetch_listings, normalize_listing
from app.services.matching_service import (
    score_batch,
    _build_listing_payload,
    _retry_with_backoff,
    _parse_response_text,
    _log_token_usage,
    _log_raw_input,
    _log_raw_output,
)

JUDGE_SYSTEM_PROMPT = """You are reviewing another AI model's real-estate matching judgments for accuracy. You are NOT scoring listings yourself.

You will be given:
1. A buyer's freeform description of what they want in a home.
2. A batch of listings, each with its full data and the ORIGINAL scorer's
   requirements breakdown: for every requirement it identified, whether it
   judged the listing as meeting that requirement (true/false), under
   "scorer_requirements", plus its overall stated reason under
   "scorer_reason".

For EACH listing, check every one of the original scorer's requirement
verdicts against the listing's actual description and structured fields.
Mark "agrees": true only if every requirement verdict for that listing is
genuinely supported by the listing's real text/data. If even one verdict
looks wrong — met marked true with no real supporting evidence, or met
marked false despite clear supporting text — mark "agrees": false and
explain exactly which requirement and why in "judge_reason". Be a
skeptical reviewer, not a rubber stamp: your job is to find real
disagreements, not to agree by default.

Respond with ONLY a JSON array, no other text, in this exact shape:
[
  {"mls_id": "...", "agrees": true, "judge_reason": "one sentence, specific"}
]
"""

FEEDBACK_PATH = Path(__file__).resolve().parent / "judge_feedback.json"
MAX_FEW_SHOT_EXAMPLES = 8
# Caps how many past human-reviewed corrections get injected into the
# judge's prompt per call. Uncapped growth would eventually make every
# judge call slower and pricier for diminishing benefit — the most recent
# corrections are also the most likely to reflect whatever mistake pattern
# is currently recurring, so "most recent N" is a reasonable cut rather
# than every correction ever collected.


def _load_feedback() -> list[dict]:
    """Past human-reviewed corrections, collected via --review across any
    previous run. Returns [] if the file doesn't exist yet — this is
    normal on a first run, not an error."""
    if not FEEDBACK_PATH.exists():
        return []
    with open(FEEDBACK_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_feedback(entries: list[dict]) -> None:
    with open(FEEDBACK_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def _format_few_shot_block(feedback: list[dict]) -> str:
    """Turns the most recent human-reviewed corrections into worked
    examples appended to JUDGE_SYSTEM_PROMPT — few-shot learning, not
    training. No weights change; this only shapes what the judge sees on
    its NEXT call, every time, from scratch. Returns "" if there's nothing
    to inject yet, so callers can skip the prompt addition entirely on a
    fresh install with no feedback file."""
    if not feedback:
        return ""
    recent = feedback[-MAX_FEW_SHOT_EXAMPLES:]
    lines = ["Human-reviewed examples from past runs — learn from these:"]
    for entry in recent:
        # .get(), not [] — entries saved before scorer_requirements was
        # added to this function's output (see _interactive_review) won't
        # have this key. Old entries still work, just without this detail.
        requirements = entry.get("scorer_requirements", [])
        requirements_line = f" Scorer's requirements: {_format_requirements(requirements)}." if requirements else ""
        lines.append(
            f'- Query: "{entry["query"]}" — {entry.get("address", "listing")}:'
            f'{requirements_line} '
            f'judge said agrees={entry["judge_verdict"]["agrees"]} '
            f'("{entry["judge_verdict"]["reason"]}"). '
            f'Human determined: {entry["human_verdict"]}.'
            + (f' Lesson: {entry["human_note"]}' if entry.get("human_note") else "")
        )
    return "\n".join(lines)


def _interactive_review(preferences: str, listing: dict, judge_result: dict, scorer_reason: str, scorer_requirements: list[dict]) -> dict | None:
    """Prompts the person running the script to say who was actually
    right about ONE verdict, right after it's printed. Returns a feedback
    entry to append to judge_feedback.json, or None if skipped. Only
    called when --review is passed — this is interactive and blocks on
    stdin, so it must never run during an unattended/automated pass.

    Shows the listing's actual description and the itemized per-requirement
    breakdown, not just the free-text reason from either model — a human
    can't meaningfully judge "was this verdict right" from a one-sentence
    summary alone, they need the same source material (the listing's real
    text) and the same granular claims (which specific requirement, met or
    not) the scorer and judge were actually working from."""
    print(f"\n    Listing description: {listing.get('description') or '(none)'}")
    if scorer_requirements:
        print("    Scorer's per-requirement breakdown:")
        for r in scorer_requirements:
            print(f"      - {r.get('text', '?')}: {'MET' if r.get('met') else 'NOT MET'}")
    else:
        print("    (Scorer returned no itemized requirements breakdown for this listing.)")
    print("    Who's actually right?")
    print("      [j] judge was right   [s] scorer was right   [b] both wrong   [enter] skip")
    choice = input("    > ").strip().lower()

    verdict_map = {"j": "judge_right", "s": "scorer_right", "b": "both_wrong"}
    human_verdict = verdict_map.get(choice)
    if human_verdict is None:
        return None  # skipped — no feedback recorded for this one

    note = input("    Optional one-line lesson for next time (enter to skip): ").strip()

    return {
        "query": preferences,
        "mls_id": str(listing.get("mls_id")),
        "address": listing.get("address") or "Unknown",
        "judge_verdict": {"agrees": judge_result.get("agrees", False), "reason": judge_result.get("judge_reason", "")},
        "scorer_reason": scorer_reason,
        "scorer_requirements": scorer_requirements,  # the itemized breakdown shown above — was
        # displayed to the human making this call but previously never actually saved, meaning
        # every future few-shot example built from it would silently drop the exact structured
        # evidence (which requirement, met or not) the human's decision was actually based on.
        "human_verdict": human_verdict,
        "human_note": note or None,
    }


def _opposite_provider(provider: str) -> str:
    """Whichever provider ISN'T the one that produced the scores being
    judged. Self-review from the same model risks sharing the exact blind
    spots it's supposed to be checking for — see README for why this
    matters more than it might seem.

    With three providers now (not the original two), "opposite" needs an
    explicit tie-break rule instead of a simple swap — there are two
    candidates to choose between, not one. Fixed preference order:
    anthropic, then openai, then vertex; pick the first one that isn't
    the scoring provider itself. This preserves the exact original
    anthropic<->openai behavior when only those two are in play, and
    gives vertex a sensible, deterministic judge (anthropic) rather than
    never being selectable as a judge at all — the bug this replaced."""
    preference_order = ("anthropic", "openai", "vertex")
    for candidate in preference_order:
        if candidate != provider:
            return candidate
    return provider  # unreachable unless VALID_AI_PROVIDERS shrinks to one


def _model_for_provider(provider: str) -> str:
    """The configured model name for whichever provider this is — used
    only for the human-readable "Scored by: X (model)" print lines.
    A plain if/elif here on purpose, not a two-way ternary — a ternary is
    exactly what caused this to silently mislabel Vertex as OpenAI's
    model (gpt-5.6-luna) before, since it only ever checked for
    "anthropic" and fell through to OpenAI's model name for anything
    else, including vertex."""
    if provider == "anthropic":
        return settings.ANTHROPIC_MODEL
    if provider == "openai":
        return settings.OPENAI_MODEL
    return settings.VERTEX_MODEL


def _build_judge_payload(listings_batch: list[dict], verdicts_by_id: dict) -> list[dict]:
    """Same listing fields the original scorer saw — reuses
    _build_listing_payload directly so the judge's view of a listing can
    never silently drift out of sync with what scoring actually sends —
    plus that listing's own requirements breakdown for the judge to
    review."""
    payload = _build_listing_payload(listings_batch)
    for item in payload:
        verdict = verdicts_by_id.get(str(item["mls_id"]), {})
        item["scorer_requirements"] = verdict.get("requirements", [])
        item["scorer_reason"] = verdict.get("reason", "")
    return payload


def _judge_batch_anthropic(user_preferences: str, listings_batch: list[dict], verdicts_by_id: dict, few_shot_block: str = "") -> list[dict]:
    import anthropic

    if not settings.ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY is not set in .env — required to judge with Anthropic.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=settings.REQUEST_TIMEOUT_SECONDS)
    system_prompt = f"{JUDGE_SYSTEM_PROMPT}\n\n{few_shot_block}" if few_shot_block else JUDGE_SYSTEM_PROMPT
    user_message = (
        f"Buyer wanted: {user_preferences}\n\n"
        f"Listings and the original scorer's verdicts:\n"
        f"{json.dumps(_build_judge_payload(listings_batch, verdicts_by_id), indent=2)}"
    )
    _log_raw_input("Anthropic (judge)", listings_batch, user_message)

    def call():
        return client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=settings.MAX_TOKENS,
            temperature=settings.TEMPERATURE,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

    response = _retry_with_backoff(call)
    _log_token_usage("Anthropic (judge)", len(listings_batch), response.usage.input_tokens, response.usage.output_tokens)
    _log_raw_output("Anthropic (judge)", listings_batch, response.content[0].text)
    return _parse_response_text(response.content[0].text)


def _judge_batch_openai(user_preferences: str, listings_batch: list[dict], verdicts_by_id: dict, few_shot_block: str = "") -> list[dict]:
    from openai import OpenAI

    if not settings.OPENAI_API_KEY:
        print("OPENAI_API_KEY is not set in .env — required to judge with OpenAI.")
        sys.exit(1)

    client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=settings.REQUEST_TIMEOUT_SECONDS)
    system_prompt = f"{JUDGE_SYSTEM_PROMPT}\n\n{few_shot_block}" if few_shot_block else JUDGE_SYSTEM_PROMPT
    user_message = (
        f"Buyer wanted: {user_preferences}\n\n"
        f"Listings and the original scorer's verdicts:\n"
        f"{json.dumps(_build_judge_payload(listings_batch, verdicts_by_id), indent=2)}"
    )
    _log_raw_input("OpenAI (judge)", listings_batch, user_message)

    def call():
        kwargs = {
            "model": settings.OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "max_completion_tokens": settings.MAX_TOKENS,
        }
        # Same reasoning as matching_service._score_batch_openai — newer
        # OpenAI models reject a custom temperature unless reasoning is off.
        if settings.OPENAI_REASONING_EFFORT:
            kwargs["reasoning_effort"] = settings.OPENAI_REASONING_EFFORT
        if settings.OPENAI_REASONING_EFFORT == "none":
            kwargs["temperature"] = settings.TEMPERATURE
        return client.chat.completions.create(**kwargs)

    response = _retry_with_backoff(call)
    _log_token_usage("OpenAI (judge)", len(listings_batch), response.usage.prompt_tokens, response.usage.completion_tokens)
    _log_raw_output("OpenAI (judge)", listings_batch, response.choices[0].message.content)
    return _parse_response_text(response.choices[0].message.content)


def judge_batch(user_preferences: str, listings_batch: list[dict], verdicts_by_id: dict, judge_provider: str, few_shot_block: str = "") -> list[dict]:
    if judge_provider == "openai":
        return _judge_batch_openai(user_preferences, listings_batch, verdicts_by_id, few_shot_block)
    return _judge_batch_anthropic(user_preferences, listings_batch, verdicts_by_id, few_shot_block)


def _format_requirements(reqs: list[dict]) -> str:
    """Compact one-line rendering of the itemized requirements breakdown,
    for the terminal. E.g. "quiet street: MET | updated kitchen: NOT MET".
    Used both in the scrolling [AGREE]/[DISAGREE] output and the CSV
    export — this is the actual evidence a human needs to independently
    verify a verdict, not just either model's own one-sentence summary
    of itself."""
    if not reqs:
        return "(no itemized requirements returned)"
    return " | ".join(f"{r.get('text', '?')}: {'MET' if r.get('met') else 'NOT MET'}" for r in reqs)


def run_judge(preferences: str, listings: list[dict], scoring_provider: str, judge_provider: str, few_shot_block: str = "", review: bool = False, new_feedback: list = None, spot_check_agrees: int = None, csv_rows: list = None, existing_feedback: list = None) -> dict:
    print(f"\n--- \"{preferences}\" ---")
    scoring_model = _model_for_provider(scoring_provider)
    judge_model = _model_for_provider(judge_provider)
    print(f"Scored by: {scoring_provider} ({scoring_model})")
    print(f"Judged by: {judge_provider} ({judge_model})")

    # Index listings by mls_id up front — needed by _interactive_review to
    # show the listing's actual description, not just the address.
    listings_by_id = {str(l["mls_id"]): l for l in listings}

    # Score every listing first, same batching real searches use.
    verdicts_by_id = {}
    for i in range(0, len(listings), settings.BATCH_SIZE):
        batch = listings[i:i + settings.BATCH_SIZE]
        for r in score_batch(preferences, batch, ai_provider=scoring_provider):
            verdicts_by_id[str(r["mls_id"])] = r

    # Judge every listing, same batching, opposite provider. Past
    # human-reviewed corrections (few_shot_block) are injected into every
    # judge call automatically, whether or not --review is on THIS run —
    # --review only controls whether NEW corrections get collected now.
    disagreements = []
    agree_count = 0
    total = 0
    agree_seen = 0  # counts AGREEs seen so far, for the spot_check_agrees cadence below
    for i in range(0, len(listings), settings.BATCH_SIZE):
        batch = listings[i:i + settings.BATCH_SIZE]
        for jr in judge_batch(preferences, batch, verdicts_by_id, judge_provider, few_shot_block):
            total += 1
            mls_id = str(jr.get("mls_id"))
            reason = jr.get("judge_reason", "")
            scorer_verdict = verdicts_by_id.get(mls_id, {})
            scorer_reason = scorer_verdict.get("reason", "")
            scorer_requirements = scorer_verdict.get("requirements", [])
            requirements_line = _format_requirements(scorer_requirements)
            listing = listings_by_id.get(mls_id, {"mls_id": mls_id})

            if csv_rows is not None:
                csv_rows.append({
                    "query": preferences,
                    "mls_id": mls_id,
                    "address": listing.get("address") or "Unknown",
                    "description": listing.get("description") or "",
                    "scorer_reason": scorer_reason,
                    "scorer_requirements": requirements_line,
                    "judge_agrees": jr.get("agrees", False),
                    "judge_reason": reason,
                })

            if jr.get("agrees"):
                agree_count += 1
                agree_seen += 1
                print(f"  [AGREE]    {mls_id} — {reason}")
                print(f"             requirements: {requirements_line}")
                # Agreement is not proof of correctness — judge and scorer
                # can share the exact same blind spot and agree while both
                # being wrong, which a disagreement-only review would never
                # surface. spot_check_agrees pulls every Nth AGREE into the
                # same review flow as a genuine disagreement, specifically
                # to give that failure mode a chance to be caught too.
                if review and new_feedback is not None and spot_check_agrees and agree_seen % spot_check_agrees == 0:
                    print("    (spot-check: reviewing this AGREE, not a disagreement)")
                    entry = _interactive_review(preferences, listing, jr, scorer_reason, scorer_requirements)
                    if entry:
                        new_feedback.append(entry)
                        if existing_feedback is not None:
                            _save_feedback(existing_feedback + new_feedback)
                            print(f"    (saved — {len(existing_feedback) + len(new_feedback)} correction(s) now in {FEEDBACK_PATH.name})")
            else:
                disagreements.append({"mls_id": mls_id, "judge_reason": reason, "scorer_reason": scorer_reason})
                print(f"  [DISAGREE] {mls_id} — judge: {reason}")
                print(f"             requirements: {requirements_line}")
                print(f"             scorer said: {scorer_reason}")
                if review and new_feedback is not None:
                    entry = _interactive_review(preferences, listing, jr, scorer_reason, scorer_requirements)
                    if entry:
                        new_feedback.append(entry)
                        # Saved immediately, not batched to the end of main() —
                        # a long run (or one you Ctrl+C out of, or check the
                        # file mid-run to look at) must not lose or hide
                        # corrections that already happened. Re-reads
                        # existing_feedback + everything collected so far on
                        # EVERY new entry, which is redundant I/O for a long
                        # run, but judge_feedback.json is small (a handful of
                        # KB even with hundreds of entries) and this only
                        # happens right after a human just finished typing,
                        # never in a hot loop — the redundancy costs nothing
                        # real and buys real durability.
                        if existing_feedback is not None:
                            _save_feedback(existing_feedback + new_feedback)
                            print(f"    (saved — {len(existing_feedback) + len(new_feedback)} correction(s) now in {FEEDBACK_PATH.name})")

    return {"total": total, "agree": agree_count, "disagree": len(disagreements), "disagreements": disagreements}


def main():
    parser = argparse.ArgumentParser(description="LLM-as-judge: has the other configured provider review score_batch's real verdicts.")
    parser.add_argument(
        "--full-sweep", action="store_true",
        help="Judge DATA_SOURCE's actual dataset instead of the fixed 14-listing realistic set. Real API cost — see README.",
    )
    parser.add_argument(
        "--sample", type=int, default=None,
        help="With --full-sweep, judge a random sample of this many listings instead of the whole dataset.",
    )
    parser.add_argument(
        "--query", default="quiet street, updated kitchen, home office space",
        help="With --full-sweep, the single preference query to judge against (default matches analyze_scores.py's default).",
    )
    parser.add_argument(
        "--review", action="store_true",
        help="After each disagreement, ask interactively who was actually right and save the answer to "
             "scripts/judge_feedback.json. Opt-in and blocks on stdin — leave off for an unattended run. "
             "Past feedback (from any prior --review run) is always used as few-shot examples regardless "
             "of whether this flag is set now.",
    )
    parser.add_argument(
        "--spot-check-agrees", type=int, default=None,
        help="With --review, also interactively review every Nth [AGREE] (not just every [DISAGREE]). "
             "Judge and scorer agreeing is not proof either is correct — they can share the exact same "
             "blind spot and agree while both being wrong, which disagreement-only review would never "
             "surface. Off by default (agrees are never spot-checked unless this is set).",
    )
    args = parser.parse_args()

    scoring_provider = settings.AI_PROVIDER
    if scoring_provider not in VALID_AI_PROVIDERS:
        print(f"AI_PROVIDER must be one of {VALID_AI_PROVIDERS}, got '{scoring_provider}'")
        sys.exit(1)

    judge_provider = _opposite_provider(scoring_provider)
    judge_key_missing = (
        (judge_provider == "anthropic" and not settings.ANTHROPIC_API_KEY)
        or (judge_provider == "openai" and not settings.OPENAI_API_KEY)
    )
    if judge_key_missing:
        print(
            f"AI_PROVIDER is '{scoring_provider}', so the judge needs the OTHER provider "
            f"('{judge_provider}') — but its API key isn't set in .env. The judge always uses "
            f"whichever provider you're NOT scoring with, so both ANTHROPIC_API_KEY and "
            f"OPENAI_API_KEY need to be set for this script to run."
        )
        sys.exit(1)

    if args.full_sweep:
        raw = fetch_listings(HardFilters())  # whatever DATA_SOURCE is set to in .env
        listings = [normalize_listing(r) for r in raw]
        if args.sample and args.sample < len(listings):
            listings = random.sample(listings, args.sample)
        print(
            f"FULL SWEEP: judging {len(listings)} listings from DATA_SOURCE={settings.DATA_SOURCE}. "
            f"This makes real API calls for BOTH scoring and judging — see README for cost."
        )
        queries = [args.query]
    else:
        raw = fetch_listings(HardFilters(cities=["Redwood City"]), data_source="realistic")
        listings = [normalize_listing(r) for r in raw]
        print(
            f"Judging against the same {len(listings)} fixed, hand-verified listings Tier 3 uses "
            f"(app/data/realistic_listings.json) and the same 3 queries."
        )
        queries = ["quiet street", "a home office", "walkable to Caltrain"]

    existing_feedback = _load_feedback()
    few_shot_block = _format_few_shot_block(existing_feedback)
    if existing_feedback:
        print(f"Loaded {len(existing_feedback)} past human-reviewed correction(s) — "
              f"using the most recent {min(len(existing_feedback), MAX_FEW_SHOT_EXAMPLES)} as few-shot examples.")
    if args.review:
        print("--review is on: you'll be asked to weigh in on every disagreement below.\n")

    new_feedback = []
    csv_rows = []
    all_results = [
        run_judge(q, listings, scoring_provider, judge_provider, few_shot_block, args.review, new_feedback, args.spot_check_agrees, csv_rows, existing_feedback)
        for q in queries
    ]

    total = sum(r["total"] for r in all_results)
    agree = sum(r["agree"] for r in all_results)
    disagree = sum(r["disagree"] for r in all_results)

    print(f"\n{'=' * 70}")
    if total:
        print(f"Judge agreement: {agree}/{total} ({100 * agree / total:.0f}%)")
    else:
        print("No results — nothing was judged.")
    if disagree:
        print(f"\n{disagree} disagreement(s) flagged for human review — see [DISAGREE] lines above.")
        print("Remember: a disagreement means a second model reached a different conclusion,")
        print("not that the original scorer was wrong. You make the final call.")
    if new_feedback:
        # Already saved incrementally in run_judge() after every single
        # entry — not re-saved here, just reported. Redoing the write here
        # too would be harmless (same data) but pointless.
        print(f"\n{len(new_feedback)} new correction(s) saved to {FEEDBACK_PATH.name} as you went "
              f"({len(existing_feedback) + len(new_feedback)} total) — used as few-shot examples on future runs.")

    # CSV export — the terminal already prints the requirements breakdown
    # under every [AGREE]/[DISAGREE] line, but scrolling text is a poor
    # tool for actually reviewing more than a handful of listings. Same
    # reasoning as analyze_scores.py's CSV export: a spreadsheet gives you
    # sortable, filterable, non-truncated columns to independently verify
    # accuracy against — the address, the listing's real description, the
    # scorer's per-requirement breakdown, and the judge's verdict, all in
    # one row per listing per query.
    if csv_rows:
        csv_path = Path(__file__).resolve().parent / "llm_judge_output.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "query", "mls_id", "address", "description",
                "scorer_reason", "scorer_requirements", "judge_agrees", "judge_reason",
            ])
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"\nFull results (every listing, every query, requirements included) written to: {csv_path}")

    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
