# HomeLens

Search single-family/condo listings with structured filters (price, beds,
schools, HOA, accessibility), then have AI re-rank results by reading each
listing's actual description against your freeform preferences.

## Project structure

```
HomeLens/
├── app/
│   ├── main.py              # FastAPI app assembly — run this with uvicorn
│   ├── config.py             # Settings, all driven by .env — no editing code to change behavior
│   ├── models.py              # Pydantic request/response schemas
│   ├── routers/
│   │   ├── listings.py        # POST /listings — hard filters only, never calls AI
│   │   └── match.py           # POST /match — hard filters + AI scoring
│   ├── services/
│   │   ├── listings_service.py   # Fetch + filter logic, all data sources
│   │   ├── matching_service.py   # AI scoring, Anthropic or OpenAI behind one switch
│   │   └── schools_service.py    # School ratings lookup — SQLite-backed in this variant
│   └── data/                  # generated/realistic JSON + schools.json + schools.db
├── scripts/
│   ├── generate_listings.py   # Regenerate the large synthetic dataset
│   ├── analyze_scores.py      # See real AI score distributions (for tuning SCORE_THRESHOLD)
│   ├── verify_test_cases.py   # Hard invariants + keyword snapshot + AI accuracy regression test
│   ├── llm_judge.py            # LLM-as-judge: opposite provider reviews score_batch's real verdicts
│   ├── seed_schools_db.py     # Builds schools.db from schools.json — run before first start
│   └── run_cli.py             # Standalone pipeline run, no API server needed
├── frontend/                  # Dependency-free React UI (React+Babel via CDN, no build step)
├── .env.example                # Copy to .env and fill in
└── requirements.txt
```

**Why this layout:** routers handle HTTP only (parse request, call a service,
shape the response); services hold the actual logic (fetching, filtering,
scoring) and have no idea they're being called from an API — you could call
them from a CLI script, a cron job, or a test with zero changes, which is
exactly what `scripts/run_cli.py` and `scripts/analyze_scores.py` already do.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
python scripts/seed_schools_db.py
```

Open `.env` and set:
- Your real `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY` if using `AI_PROVIDER=openai`)
- `DATA_SOURCE` — start with `generated` for a large, varied test set

## Running the API

```bash
uvicorn app.main:app --reload
```
(must be run from the project root — the `app.main:app` path depends on it)

Open **http://127.0.0.1:8000/docs** for interactive Swagger docs.

## Running the frontend

```bash
cd frontend
python3 -m http.server 5500
```
Open **http://127.0.0.1:5500**. It calls `http://127.0.0.1:8000` by default —
change `API_BASE` at the top of `frontend/app.jsx` if your API runs elsewhere.

## Switching data source or AI provider

Two ways to do this, depending on what you're after:

**Live, per-search toggle (no restart needed)** — the "Source" and "Matched
by" dropdowns in the app header let you switch on the fly and apply
immediately to your very next search. This only affects that one request —
it doesn't change any file, and resets back to the `.env` default the next
time the server restarts. Good for quickly comparing sources or providers
side by side.

**Permanent default** — change `.env`:
```
DATA_SOURCE=realistic     # or: live, generated
AI_PROVIDER=openai        # or: anthropic, vertex
```
Restart `uvicorn` (or let `--reload` pick it up) after changing `.env`. This
is what the header dropdowns default to on page load, and what any request
uses if it doesn't specify an override.

**`vertex` (Gemini) is set up differently from the other two** — no API
key. It authenticates via Google Cloud's Application Default Credentials
instead: locally, run `gcloud auth application-default login` once; on
Cloud Run, grant the service's own service account the "Vertex AI User"
IAM role (no secret to manage at all in that case). You also need
`GCP_PROJECT_ID` set in `.env` — see `.env.example` for the full setup.

**One practical gotcha when switching to `live`:** the City filter defaults
to "Redwood City" (correct for `realistic`/`generated`, which are
our own fixed data), but `live` is SimplyRETS' real sandbox and its listings
are in Houston, not Redwood City — clear or change the City field when
testing `live`, or you'll get zero results for reasons that have nothing to
do with your other filters.

## Data sources, what each is for

- **`live`** — real SimplyRETS sandbox API. Small (tens of listings, not
  hundreds), and its `remarks` field is identical boilerplate text on every
  listing — not useful for testing AI matching quality, only for seeing what
  a real MLS feed's response shape looks like.
  **Nothing about this source is fixed** — it's a third-party demo service,
  and its contents can and do change without any action on our end. We've
  observed the total listing count change (65 one day, 45 the next) for
  the city of Houston. In case city could change as we don't own that data, don't
  hardcode an assumed city or count anywhere against this source — check
  what's actually in it first:
  ```bash
  curl -u simplyrets:simplyrets "https://api.simplyrets.com/properties?limit=5" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); [print(l['address']['city']) for l in d]"
  ```
- **`realistic`** — 14 hand-written Redwood City listings with real pricing,
  varied descriptions, school assignments, HOA fees, and a mix of single/
  multi-story and condo/single-family — good for demoing specific scenarios.
  Also the dataset `scripts/verify_test_cases.py` uses for its AI accuracy
  regression test, specifically because it's small and fixed (see below).
- **`generated`** — large (500+ by default) dataset combining real
  neighborhoods with randomized-but-meaningful description templates. Run
  `python scripts/generate_listings.py 2000` to regenerate at any size.
  This is the one to use for realistic-scale filter and matching tests.

  **No fixed random seed:** every run of `generate_listings.py` re-rolls
  *everything* — prices, addresses, beds, stories, descriptions, schools,
  all of it — not just whatever you meant to change. If you're tracking
  specific test-case numbers (e.g. "min_price=2M returns 283 listings"),
  those numbers will drift the next time anyone regenerates the dataset.
  Re-verify against the current file rather than trusting old numbers.

**Cost/latency note at scale:** AI matching batches `BATCH_SIZE` (default 8)
listings per API call. At 500+ listings, that's 60+ calls per search — real
latency and cost, unlike testing against 14 listings. Use hard filters
(price/beds/city/etc.) to narrow the pool before it reaches AI scoring,
exactly like a real production system would — never send an entire
inventory to an LLM per search. Or use "Browse all (skip AI)" in the
frontend to test filters alone with zero AI cost.

**Making matching faster:**
- **`MAX_CONCURRENT_BATCHES`** (default 4) — batches run in parallel, not
  one at a time. Raising this speeds up total wall-clock time for a large
  search, but pushes more simultaneous requests at your AI provider.
  **Don't guess this — check the actual evidence.** Every API call prints a
  line like `[Anthropic rate limits] requests: 9999/10000 remaining` to your
  terminal. If that number stays close to your limit with no retry/429 lines
  showing up, you have real headroom and can raise this — try doubling it
  (e.g. 4 → 8), confirm it's still clean, and go from there. If you start
  seeing `[rate limit] Hit 429, retrying...` lines or the UI's retry-count
  warning, that's your signal you've gone too high for your current account
  tier. Cancellation still works correctly regardless of this setting — no
  new batches get submitted once cancelled, but whichever are already in
  flight are allowed to finish rather than abandoned mid-request.
- **`ANTHROPIC_MODEL` / `OPENAI_MODEL`** — swap to a smaller/faster tier
  for quicker, cheaper calls. Verify accuracy with
  `python scripts/verify_test_cases.py --with-ai` before committing to a
  smaller model — it's a real regression test, not a vibe check.
  `claude-haiku-4-5-20251001` and `gpt-5.6-luna` both pass cleanly.

  **`TEMPERATURE` only applies to the Anthropic path by default.** Newer
  OpenAI models (gpt-5.6+) reject a custom `temperature` value unless
  `OPENAI_REASONING_EFFORT=none` is also set (see `.env.example`) — without
  it, Luna runs at its own default temperature, not `TEMPERATURE`'s value.
  This is optional, not required — Luna passes the accuracy test fine
  either way.

## Schools

All school names in this project (`app/data/schools.json`, and the school
assignments in `realistic_listings.json` and `generated_listings.json`) are
entirely fictional — invented names like Cedar Ridge Elementary, Warren
Middle School, and Charter High School. None correspond to real institutions,
and none of the ratings are real either. Treat all of it as synthetic test
data only.

School assignment is tied to each listing's (also fictional) neighborhood —
every listing in a given neighborhood shares the same three schools, similar
to how real school district boundaries work — rather than assigned randomly
per listing.

### This is the SQLite variant

This copy of the project stores school ratings in a real SQLite database
(`app/data/schools.db`) instead of parsing `schools.json` on every request.
`schools.json` still exists — it's the source of truth you'd hand-edit —
but the app actually queries `schools.db`, built from it by:

```bash
python scripts/seed_schools_db.py
```

Run that once before starting the app, and again any time you edit
`schools.json`. No new dependency needed — `sqlite3` ships with Python.

**Why this is safe to deploy, including on hosts with an ephemeral
filesystem (like Render's free tier):** `schools.db` is read-only at
runtime — nothing in the app ever writes to it after the seed script runs.
It's built once, committed to the repo like `generated_listings.json`
already is, and re-created fresh on every deploy. This is a different
situation from a runtime cache or search history, which *would* break on
a host that wipes local files on every restart/spin-down.

## Tuning `SCORE_THRESHOLD`

Don't guess it — measure it. Run:
```bash
python scripts/analyze_scores.py "your test preferences"
```
This scores every listing with the threshold effectively disabled, prints
every score, and gives you min/max/average plus counts above 40/50/60/70 —
set `SCORE_THRESHOLD` in `.env` based on where a real quality gap appears in
your actual data's distribution, not a number that sounds reasonable.

## Verifying the dataset and AI accuracy: `scripts/verify_test_cases.py`

```bash
python scripts/verify_test_cases.py                  # hard invariants + keyword snapshot
python scripts/verify_test_cases.py --update-baseline # after intentionally regenerating data
python scripts/verify_test_cases.py --with-ai         # + a real AI accuracy regression test
```

Three different kinds of checks — **if one fails, the right response is
different depending on which, and it matters:**

- **A hard invariant fails** (e.g. "every condo is single-story") — this
  means `generate_listings.py`'s own logic broke a guarantee it's supposed
  to always hold. `--update-baseline` does nothing for this. Go fix the
  generator's code.
- **The keyword snapshot fails** (e.g. "quiet: expected 202, got 197") — ask
  whether you just ran `generate_listings.py` on purpose:
  - **Yes** → `--update-baseline` is correct; the dataset is supposed to
    have different keyword frequencies after a regeneration.
  - **No** → don't run `--update-baseline` reflexively just to clear the
    failure. Investigate first — this means something changed the data or
    the generator unexpectedly, which is a real regression, not something to
    silently accept.
- **The `--with-ai` accuracy test fails** — this is a real regression test,
  not a spot-check: it runs against the small, fixed `realistic` dataset
  (never `DATA_SOURCE`'s current value) and asserts specific listings score
  exactly what they should, based on ground truth hand-verified by reading
  each listing's actual remarks text (e.g. 1287 Woodside Rd explicitly says
  "busy arterial street," so it must score 0 for "quiet street"). A failure
  here means the AI is actually misclassifying something — a real signal to
  use after changing `ANTHROPIC_MODEL`/`OPENAI_MODEL`, `TEMPERATURE`, or the
  system prompt, to catch an accuracy regression rather than just a vibe
  check. There's no baseline to update here — a failure is either a genuine
  model/prompt regression to fix, or (rarely) a sign the ground truth
  assertions themselves need revisiting if `realistic_listings.json` is
  ever intentionally edited.

## LLM-as-judge accuracy check: `scripts/llm_judge.py`

```bash
python scripts/llm_judge.py                                     # default: 14 listings, 3 queries, ~12 calls
python scripts/llm_judge.py --full-sweep --sample 50             # a 50-listing sample of DATA_SOURCE instead
python scripts/llm_judge.py --full-sweep --query "quiet street, walkable to Caltrain"
python scripts/llm_judge.py --scorer openai --judge vertex       # pick both providers explicitly
python scripts/llm_judge.py --judge anthropic                    # keep AI_PROVIDER as scorer, override just the judge
```

By default, has whichever provider you're **not** scoring with review the
verdicts — `AI_PROVIDER` picks the scorer, `llm_judge.py` picks the judge
automatically using a fixed preference order (anthropic, then openai,
then vertex — first one that isn't the scorer). Score with `anthropic`,
judged by `openai` (same as before Vertex existed); score with `vertex`,
judged by `anthropic`.

**`--scorer`/`--judge` override either side explicitly** — pass one and
the other still falls back to the rule above; pass both and both are used
exactly as given, including picking the *same* provider for both (prints
a self-review warning and proceeds — self-review risks the judge sharing
the exact blind spot it's supposed to be checking for, but nothing stops
you from doing it deliberately). Whichever providers end up in play, each
review `score_batch`'s real verdicts for whether each requirement
judgment is actually supported by the listing's text. Not a replacement
for the `--with-ai` test above, which is real hand-verified ground truth;
a judge model isn't inherently more trustworthy than the model being
judged, and if both share the same blind spot they'll agree while both
being wrong. What it's for: **triage**. Instead of reading through every
listing yourself, you get a shortlist of exactly which verdicts a second
model disagreed with, worth a human look — a `[DISAGREE]` line is a
prioritization signal, not proof of an error. You still make the final
call, the same way you already do for the `--with-ai` ground truth above.

Every `[AGREE]`/`[DISAGREE]` line prints a `requirements:` line right
underneath it — the actual itemized `requirement text: MET/NOT MET`
breakdown, not just either model's one-sentence summary of itself. A
summary alone ("all verdicts are supported...") isn't something you can
independently verify against; the itemized breakdown is. Every run also
writes `scripts/llm_judge_output.csv` — every listing, every query, the
listing's real description, the full requirements breakdown, and the
judge's verdict, all in one row. Same reasoning as `analyze_scores.py`'s
CSV export: scrolling terminal text is a poor tool for reviewing more
than a handful of listings; a spreadsheet gives you sortable, filterable,
non-truncated columns instead.

Whichever providers end up scoring and judging need their credentials
available — `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` in `.env` for those two,
or working Application Default Credentials plus `GCP_PROJECT_ID` for
Vertex — the script checks **both** up front (not just the judge — the
scorer can now be overridden away from `AI_PROVIDER` too, so it's no
longer guaranteed pre-validated at app startup) and tells you exactly
what's missing rather than failing partway through a run.

Judging happens in the same `BATCH_SIZE`-sized batches scoring already
uses, not one call per listing — reviewing N listings costs `ceil(N/8)`
calls, not N:

| Scope | Scoring calls | Judge calls | Total |
|---|---|---|---|
| Default — same 14 listings + 3 queries `--with-ai` uses above | 6 | 6 | **12** |
| `--full-sweep` against `generated` (500 listings), 1 query | 63 | 63 | **126** |
| `--full-sweep --sample 50`, 1 query | 7 | 7 | **14** |

For an exact dollar cost rather than a call count, run with
`DEBUG_MODE=true` (see above) to log real token usage, then apply your
provider's published per-token rate.

**Getting more accurate over time — `--review`:**

```bash
python scripts/llm_judge.py --review
```

After each `[DISAGREE]`, asks interactively who was actually right —
`[j]` judge / `[s]` scorer / `[b]` both wrong / `[enter]` skip — plus an
optional one-line lesson, and saves your answer to
`scripts/judge_feedback.json` **immediately** — not deferred until the
script finishes every query. Check the file mid-run and your review is
already there; Ctrl+C or a crash loses at most whatever you were mid-way
through answering, never the whole run. On every future run (with or
without `--review` — the flag only controls whether *this* run collects
new corrections), the most recent corrections get folded into the judge's
prompt as worked examples. This is few-shot learning from your
corrections, not training — no model weights change, and there's no
version of this where review stops being useful entirely. What
realistically improves is the *frequency* of disagreements needing your
attention, not the review disappearing. `--review` is opt-in specifically
because it blocks on keyboard input after every disagreement — leave it
off for an unattended run.

The review prompt shows the listing's actual description and the scorer's
itemized per-requirement breakdown (`requirement text: MET/NOT MET`), not
just a one-sentence summary — you need the same source material the
models had to meaningfully judge a verdict, not just their own account of
it.

**Agreement isn't proof of correctness — `--spot-check-agrees`:**

```bash
python scripts/llm_judge.py --review --spot-check-agrees 5
```

`[DISAGREE]` isn't the only thing worth a second look. If the scorer and
judge share the same blind spot, they'll agree confidently while both
being wrong — and a disagreement-only review would never catch that,
since nothing ever flags it. `--spot-check-agrees N` pulls every Nth
`[AGREE]` into the same review flow as a real disagreement. Off by
default; only takes effect together with `--review`.

## Ground-truth accuracy dashboard: `scripts/eval_dashboard.py`

```bash
python scripts/eval_dashboard.py                    # AI_PROVIDER only
python scripts/eval_dashboard.py --provider openai   # one specific provider
python scripts/eval_dashboard.py --compare-providers # all 3 side by side (3x the calls)
```

Renders `scripts/eval_dashboard.html` — an offline-viewable report scored
against **real ground truth**, not another model's opinion. This is the
difference from `llm_judge.py` above: the judge script has one model
review another's verdicts, and neither is inherently more correct if
they share a blind spot. This script instead scores against
`scripts/golden_dataset.py` — the single hand-verified answer key both
this script and `verify_test_cases.py`'s Tier 3 import from, so the two
can never drift out of sync — where every expected answer was verified
by a human actually reading the listing's remarks text (and, for
schools, the real ratings in `app/data/schools.json`) in
`app/data/realistic_listings.json`.

**Coverage — the full golden dataset, not a sample:**

- **8 single-requirement dimensions**, each with real positive AND
  negative listings: quiet street, home office, walkable to Caltrain,
  single-story/no-stairs, low-maintenance condo (HOA), newer
  construction (2000+), large lot (8,000+ sqft), and one **negation**
  case — `definitely not a ranch-style home` — the exact example
  `SYSTEM_PROMPT` itself uses ("style ... e.g. 'not a ranch'"), now
  actually checked against ground truth for the first time.
- **2 ambiguous-first dimensions** — highly-rated schools, recently-
  renovated kitchen — where a real chunk of listings are deliberately
  left ungraded because a confident answer depends on an interpretation
  call a human hasn't fixed (e.g. does "good schools" mean the
  elementary rating alone, or all three assigned schools together?).
  Those rows still show the model's real score, just no PASS/FAIL
  verdict, so you can see what the model actually said on a genuinely
  hard case without pretending there's a right answer to grade it
  against.
- **4 combined multi-requirement cases, from 2 up to 5 requirements at
  once** — e.g. `quiet street, a home office, no stairs, definitely not
  a ranch-style home, and a large lot of at least 8,000 sqft` — testing
  the deterministic met/total scoring math together with classification
  accuracy, not just a single true/false judgment.

Listings any dimension excludes as genuinely ambiguous (see the comments
next to each `_TRUE`/`_FALSE` set in `golden_dataset.py`) are still
listed in the dashboard, marked excluded — the golden dataset is never
quietly narrowed without saying so.

The HTML itself needs no server and no internet connection to view —
open it directly in a browser. Per dimension: a pass/fail accuracy bar,
a case breakdown (positive/negative/partial/ambiguous, whichever apply),
and a full table of every listing with its expected label, actual score,
PASS/FAIL (or a neutral "N/A" badge for ambiguous rows), and the model's
real reason text. `--compare-providers` additionally renders a
side-by-side accuracy comparison across all three providers, useful for
answering "which provider is actually more accurate on this data" with
a real number instead of a guess.

Same batching economy as the other scripts (`ceil(14/BATCH_SIZE)` calls
per dimension, not one per listing), and the same credential check as
`llm_judge.py` — whichever provider(s) you're evaluating need their
`.env` credentials present, checked up front. The larger dimension set
means more calls than before: roughly 28 scoring calls per provider (8
binary + 2 ambiguous + 4 combined dimensions, `ceil(14/BATCH_SIZE)` each) —
still cheap (14 fixed listings, no `--full-sweep`-style real dataset
cost), but worth knowing before running `--compare-providers` (3x that).

## Prompt caching, by provider

The `SYSTEM_PROMPT` in `matching_service.py` is identical on every scoring
call, which makes it a caching candidate — but the three providers don't
handle this the same way:

| Provider | How caching works | Status here |
|---|---|---|
| **Anthropic** | Opt-in only — you must tag a block with `cache_control`, nothing is cached automatically. The *tagged block itself* also has to clear a minimum size before anything actually gets cached (~1024 tokens for Sonnet/Opus, ~2048 for Haiku). | Tag is in place on `system` in `_score_batch_anthropic`, but currently a **no-op** — see below. |
| **OpenAI** | Fully automatic, server-side. Caches the longest matching prefix across consecutive requests once the total prompt is ≥1024 tokens. No code required. | **Working**, verified live. |
| **Vertex (Gemini 2.5)** | Fully automatic ("implicit caching"), same general mechanic as OpenAI. No code required. | **Working**, verified live — and this is the provider `AI_PROVIDER` currently defaults to. |

**Current prompt size** — measured directly via a live call (not
estimated): `SYSTEM_PROMPT` plus the thin per-batch wrapper (`"Buyer
wants: ..."` + one near-empty listing) costs **717 input tokens total**,
so `SYSTEM_PROMPT` alone is roughly **~700 tokens**.

**Where that leaves Anthropic**: 700 tokens is below *both* thresholds —
Sonnet/Opus's 1024 and Haiku's 2048 — so the `cache_control` tag does
nothing today no matter how large a batch you send. Confirmed live: even
a full 14-listing (~5850-token) call showed `cache write: 0` on both a
first and a repeat call, because the minimum applies to the tagged
block's own size, not the size of the whole request. It'll start paying
off automatically if `SYSTEM_PROMPT` ever grows past ~1024 tokens, or the
model changes — no code change needed at that point, just re-check the
logged numbers.

**Where that leaves OpenAI and Vertex**: both already cache today, no
action needed. Verified live with two back-to-back, byte-identical calls
(same query, same 14-listing batch): OpenAI's second call read
`5011/5014` input tokens from cache; Vertex's read `5040/5805`. Those
near-100% figures are an artifact of the test being an *exact* repeat,
done specifically to confirm the mechanism is real — not a forecast of
production savings. Real searches vary (different buyer text, different
listings), so the only part guaranteed identical across calls is the
~700-token `SYSTEM_PROMPT` itself; expect real-world cache hits close to
that ~700 tokens/call, not the full request.

Every provider now logs real `cache read` / `cache write` token counts on
every call (`_log_token_usage` in `matching_service.py`) — check those
numbers directly when this changes, rather than assuming caching
behavior from the code alone.

## Moving to real MLS data

Swap `DATA_SOURCE=live` for a real feed later: SimplyRETS production,
Bridge Interactive (CoreLogic Trestle), or Spark API all use a similar
RESO-based response shape — `listings_service.py`'s `_fetch_from_simplyrets`
function is the only place you'd need to touch. Zillow/Redfin/Trulia do not
offer general-purpose listing APIs; scraping them violates their terms of
service.
