"""
app/services/matching_service.py

Scores listings against a buyer's freeform preferences using whichever AI
provider is configured (settings.AI_PROVIDER). Previously this was two
separate files (semantic_match.py / semantic_match_openai.py) that required
commenting/uncommenting an import line in app.py to switch — that's fragile
and easy to forget. Now it's one env var: AI_PROVIDER=anthropic|openai.
"""

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from app.config import settings, VALID_AI_PROVIDERS


class MatchingError(Exception):
    """Carries two completely separate messages on purpose: `client_message`
    (calm, jargon-free, safe to show to a real end user — never mentions
    env vars, SDK exception types, providers, or raw API payloads) and
    `technical_detail` (everything a developer needs, logged server-side
    only via print(), never sent to the browser). Every place in this file
    that used to raise a plain RuntimeError with a developer-facing string
    raises this instead, so the two audiences are never conflated into one
    message that serves neither well."""
    def __init__(self, client_message: str, technical_detail: str):
        self.client_message = client_message
        self.technical_detail = technical_detail
        super().__init__(technical_detail)


# Standard client-facing messages, one per category of failure. Deliberately
# generic and identical regardless of which provider or exact cause — a real
# user doesn't need to know it was OpenAI vs Anthropic, a rate limit vs an
# auth failure; they need to know whether trying again might help.
_CLIENT_MSG_TRANSIENT = "We're having trouble reaching the AI service right now. Please try again in a moment."
_CLIENT_MSG_CONFIG = "Something isn't configured correctly on our end. Please try again later."
_CLIENT_MSG_TOO_LARGE = "That search returned more than we could process. Try narrowing your filters."
_CLIENT_MSG_UNKNOWN = "Something unexpected happened. Please try again."

SYSTEM_PROMPT = """You are a real estate matching assistant. You will be given:
1. A buyer's freeform description of what they want in a home.
2. A batch of listings, each with an id, basic specs (including stories,
   property_type, and hoa_fee when available), a free-text description, and
   school_ratings — a 1-10 rating for the assigned elementary/middle/high
   school, when available.

For EACH listing, do NOT compute a numeric score yourself — the calling
system computes that deterministically from the requirements breakdown you
provide below. Your job is only to identify requirements and judge each one
honestly.

Step 1 — break the buyer's preferences into distinct, named requirements.
"Quiet cul-de-sac, near Caltrain, and away from the highway" is THREE
requirements, not one or two — do not merge related-sounding ones together.
If the buyer's text is too vague to break into multiple distinct asks (e.g.
just "nice house" or "any"), use a single requirement summarizing it.

Step 2 — for each requirement, judge whether THIS listing's description (and
structured fields — school_ratings when the buyer mentions schools/kids/
family; stories and phrases like "no stairs" or "walk-in shower" when they
mention accessibility; hoa_fee/property_type when they mention condos or
low-maintenance living; year_built when they mention a construction-year
range, "newer," "historic," or similar; lot_size when they mention a "big
yard," "large lot," an acreage figure, or similar; style when they name or
rule out an architectural style, e.g. "farmhouse" or "not a ranch";
address/city/state/postal_code when they mention a specific street,
neighborhood, city, or zip code — this matters even when the buyer never
touched a separate city/location filter and is relying entirely on this
freeform text) clearly supports it: true or false. Be honest — mark true
only when the listing's actual text/data supports it, not because it seems
plausible. If a requirement is never mentioned or contradicted, mark it
false — do not give credit for silence.

Step 3 — write one specific sentence explaining the requirements breakdown,
referencing what was met and what wasn't.

Respond with ONLY a JSON array, no other text, in this exact shape:
[
  {
    "mls_id": "...",
    "requirements": [
      {"text": "short label for requirement 1", "met": true},
      {"text": "short label for requirement 2", "met": false}
    ],
    "reason": "one sentence, specific, citing what in the listing supported or failed each requirement"
  }
]
"""


def _build_listing_payload(listings_batch: list[dict]) -> list[dict]:
    return [
        {
            "mls_id": l["mls_id"],
            "price": l["price"],
            "address": l.get("address"),
            "city": l.get("city"),
            "state": l.get("state"),
            "postal_code": l.get("postal_code"),
            "beds": l["beds"],
            "baths": l["baths"],
            "sqft": l["sqft"],
            "stories": l.get("stories"),
            "property_type": l.get("property_type"),
            "style": l.get("style"),
            "hoa_fee": l.get("hoa_fee"),
            "year_built": l.get("year_built"),
            "lot_size": l.get("lot_size"),
            "description": l["description"],
            "school_ratings": l.get("school_ratings"),
        }
        for l in listings_batch
    ]


def _parse_response_text(text: str) -> list[dict]:
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print("Warning: failed to parse model output, skipping batch:\n", text)
        return []


def _retry_with_backoff(fn, max_attempts=4, base_delay=2.0, on_retry=None):
    """
    Retries fn() on rate-limit errors with exponential backoff (2s, 4s, 8s...).
    Rate limits (too many requests/tokens per minute) are expected, temporary
    conditions — especially now that batches run concurrently
    (MAX_CONCURRENT_BATCHES) — not a real failure, so retrying briefly is the
    standard approach rather than immediately surfacing an error to the user.
    Any other exception (auth, not-found, etc.) is raised immediately, since
    those won't resolve by waiting.

    on_retry(attempt, delay), if given, is called each time a retry is about
    to happen — used by the job runner to surface "this is retrying" to the
    frontend via the poll endpoint, not just as a terminal print.
    """
    import time
    import anthropic
    import openai

    for attempt in range(max_attempts):
        try:
            return fn()
        except (anthropic.RateLimitError, openai.RateLimitError):
            if attempt == max_attempts - 1:
                raise
            delay = base_delay * (2 ** attempt)
            print(f"[rate limit] Hit 429, retrying in {delay:.0f}s (attempt {attempt + 1}/{max_attempts})...")
            if on_retry:
                on_retry(attempt + 1, delay)
            time.sleep(delay)


def _log_rate_limit_headers(provider: str, headers) -> None:
    """
    Prints the current rate-limit standing after every API call, straight
    to the uvicorn terminal — the most precise, real-time source for this
    (no Console dashboard lag). Anthropic and OpenAI use different header
    names for the same concepts, so each is read separately; missing
    headers are shown as '?' rather than crashing, since header sets can
    change between SDK/API versions.
    """
    if provider == "Anthropic":
        req_remaining = headers.get("anthropic-ratelimit-requests-remaining", "?")
        req_limit = headers.get("anthropic-ratelimit-requests-limit", "?")
        tok_remaining = headers.get("anthropic-ratelimit-tokens-remaining", "?")
        tok_limit = headers.get("anthropic-ratelimit-tokens-limit", "?")
        reset = headers.get("anthropic-ratelimit-requests-reset", "?")
    else:  # OpenAI
        req_remaining = headers.get("x-ratelimit-remaining-requests", "?")
        req_limit = headers.get("x-ratelimit-limit-requests", "?")
        tok_remaining = headers.get("x-ratelimit-remaining-tokens", "?")
        tok_limit = headers.get("x-ratelimit-limit-tokens", "?")
        reset = headers.get("x-ratelimit-reset-requests", "?")

    print(
        f"[{provider} rate limits] requests: {req_remaining}/{req_limit} remaining  |  "
        f"tokens: {tok_remaining}/{tok_limit} remaining  |  resets: {reset}"
    )


def _humanize_anthropic_error(e) -> tuple[str, str]:
    """Returns (client_message, technical_detail). Extracts the actual
    message instead of the raw exception object for the technical side, and
    picks the right client-facing category based on the specific error —
    e.g. a context-length error is genuinely something the user can act on
    (narrow the search), unlike most other API errors."""
    body = getattr(e, "body", None)
    error_info = body.get("error", {}) if isinstance(body, dict) else {}
    message = error_info.get("message") or str(e)
    error_type = error_info.get("type", "")
    technical = f"Anthropic API error ({e.status_code}): {message}"

    if "context" in error_type.lower() or "too long" in message.lower() or "maximum" in message.lower():
        return _CLIENT_MSG_TOO_LARGE, technical
    return _CLIENT_MSG_CONFIG, technical


def _score_batch_anthropic(user_preferences: str, listings_batch: list[dict], on_retry=None) -> list[dict]:
    import anthropic

    if not settings.ANTHROPIC_API_KEY:
        raise MatchingError(
            _CLIENT_MSG_CONFIG,
            "ANTHROPIC_API_KEY is not set — required to use Claude as the AI provider. Add it to .env, or select a different provider.",
        )

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    user_message = f"Buyer wants: {user_preferences}\n\nListings:\n{json.dumps(_build_listing_payload(listings_batch), indent=2)}"
    _log_raw_input("Anthropic", listings_batch, user_message)

    def call():
        # with_raw_response gives access to the actual HTTP headers (rate
        # limit info) alongside the parsed response — .parse() below gets
        # you the normal Message object, same as client.messages.create()
        # would have returned directly.
        raw = client.messages.with_raw_response.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=settings.MAX_TOKENS,
            temperature=settings.TEMPERATURE,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        _log_rate_limit_headers("Anthropic", raw.headers)
        return raw.parse()

    try:
        response = _retry_with_backoff(call, on_retry=on_retry)
    except anthropic.RateLimitError as e:
        raise MatchingError(
            _CLIENT_MSG_TRANSIENT,
            "Anthropic rate limit hit repeatedly even after retrying — you're sending requests "
            "faster than your account's current limit allows. Try lowering MAX_CONCURRENT_BATCHES "
            "in .env, or check your rate limits in the Anthropic Console.",
        ) from e
    except anthropic.AuthenticationError as e:
        raise MatchingError(
            _CLIENT_MSG_CONFIG,
            "Anthropic authentication failed — check ANTHROPIC_API_KEY in .env and that billing is set up.",
        ) from e
    except anthropic.NotFoundError as e:
        raise MatchingError(
            _CLIENT_MSG_CONFIG,
            f"Model '{settings.ANTHROPIC_MODEL}' not found or not available on your account.",
        ) from e
    except anthropic.APIStatusError as e:
        raise MatchingError(*_humanize_anthropic_error(e)) from e

    _log_token_usage("Anthropic", len(listings_batch), response.usage.input_tokens, response.usage.output_tokens)
    _log_raw_output("Anthropic", listings_batch, response.content[0].text)
    return _parse_response_text(response.content[0].text)


def _log_token_usage(provider: str, batch_size: int, input_tokens: int, output_tokens: int) -> None:
    """Prints EXACT token usage straight from the API response's own usage
    field — reliable even under concurrency, unlike trying to infer usage
    from the shared rate-limit 'remaining' headers (those reflect multiple
    threads' calls interleaved against a bucket that's also continuously
    refilling, so they can't be cleanly subtracted to get a single call's
    real cost)."""
    print(f"[{provider} usage] batch of {batch_size} listings — "
          f"input: {input_tokens} tokens, output: {output_tokens} tokens, "
          f"total: {input_tokens + output_tokens} tokens")


def _log_raw_output(provider: str, listings_batch: list[dict], raw_text: str) -> None:
    """Prints the model's raw, unparsed response text — server-side only,
    same principle as every other technical log in this file (never sent
    to the browser). Useful for actually seeing what the model said,
    e.g. while tuning the system prompt or debugging a parse failure.
    Includes the batch's mls_ids so it's identifiable in a busy,
    concurrent log with several batches' output interleaved."""
    ids = [str(l.get("mls_id")) for l in listings_batch]
    print(f"[{provider} raw output] batch mls_ids={ids}:\n{raw_text}\n{'-' * 60}")


def _log_raw_input(provider: str, listings_batch: list[dict], user_message: str) -> None:
    """Same idea as _log_raw_output, but for what we actually SEND —
    the buyer's preferences plus the exact JSON payload built by
    _build_listing_payload() for this batch (SYSTEM_PROMPT itself isn't
    repeated here since it's identical on every call; this is the part
    that varies per batch and is what actually matters for debugging,
    e.g. confirming a field like year_built genuinely made it into what
    the model received, not just that it exists in our own data)."""
    ids = [str(l.get("mls_id")) for l in listings_batch]
    print(f"[{provider} raw input] batch mls_ids={ids}:\n{user_message}\n{'-' * 60}")


def _humanize_openai_error(e) -> tuple[str, str]:
    """Returns (client_message, technical_detail) — same split as
    _humanize_anthropic_error. Extracts the real message from OpenAI's
    structured error body instead of dumping the whole raw exception
    object, and picks the client-facing category based on what actually
    went wrong (a context-length error is genuinely something the user
    can act on; a config issue on our end isn't)."""
    body = getattr(e, "body", None)
    error_info = body.get("error", {}) if isinstance(body, dict) else {}
    message = error_info.get("message") or str(e)
    param = error_info.get("param")
    code = error_info.get("code")

    if param == "temperature" and code == "unsupported_value":
        technical = (
            f"OpenAI rejected the temperature setting: {message} "
            f"Fix: set OPENAI_REASONING_EFFORT=none in .env — that's the one case OpenAI "
            f"allows a custom temperature. With any other value (including unset), "
            f"temperature is never sent and this error shouldn't occur — if you're seeing "
            f"this with OPENAI_REASONING_EFFORT already set to something else, the running "
            f"server likely hasn't picked up that .env change yet (restart uvicorn)."
        )
        return _CLIENT_MSG_CONFIG, technical

    if code == "context_length_exceeded":
        return _CLIENT_MSG_TOO_LARGE, f"OpenAI API error ({e.status_code}): {message}"

    return _CLIENT_MSG_CONFIG, f"OpenAI API error ({e.status_code}): {message}"


def _score_batch_openai(user_preferences: str, listings_batch: list[dict], on_retry=None) -> list[dict]:
    import openai
    from openai import OpenAI, AuthenticationError, NotFoundError, APIStatusError

    if not settings.OPENAI_API_KEY:
        raise MatchingError(
            _CLIENT_MSG_CONFIG,
            "OPENAI_API_KEY is not set — required to use OpenAI as the AI provider. Add it to .env, or select a different provider.",
        )

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    user_message = f"Buyer wants: {user_preferences}\n\nListings:\n{json.dumps(_build_listing_payload(listings_batch), indent=2)}"
    _log_raw_input("OpenAI", listings_batch, user_message)

    def call():
        # No temperature by default — newer OpenAI models (gpt-5.6 and
        # later) reject any non-default value while reasoning is active,
        # unlike Anthropic's API. Per OpenAI's docs, temperature is only
        # accepted alongside reasoning_effort="none" specifically — so we
        # only attempt it in that one case, and let it fail loudly and
        # clearly (via the existing APIStatusError handling below) if that
        # combination isn't actually accepted for this particular model.
        # This makes the behavior empirically verifiable rather than
        # something we just assume works.
        kwargs = {
            "model": settings.OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "max_completion_tokens": settings.MAX_TOKENS,
        }
        if settings.OPENAI_REASONING_EFFORT:
            kwargs["reasoning_effort"] = settings.OPENAI_REASONING_EFFORT
        if settings.OPENAI_REASONING_EFFORT == "none":
            kwargs["temperature"] = settings.TEMPERATURE

        raw = client.chat.completions.with_raw_response.create(**kwargs)
        _log_rate_limit_headers("OpenAI", raw.headers)
        return raw.parse()

    try:
        response = _retry_with_backoff(call, on_retry=on_retry)
    except openai.RateLimitError as e:
        raise MatchingError(
            _CLIENT_MSG_TRANSIENT,
            "OpenAI rate limit hit repeatedly even after retrying — you're sending requests faster "
            "than your account's current limit allows. Try lowering MAX_CONCURRENT_BATCHES in .env, "
            "or check your rate limits at platform.openai.com.",
        ) from e
    except AuthenticationError as e:
        raise MatchingError(
            _CLIENT_MSG_CONFIG,
            "OpenAI authentication failed — check OPENAI_API_KEY in .env and that billing is set up.",
        ) from e
    except NotFoundError as e:
        raise MatchingError(
            _CLIENT_MSG_CONFIG,
            f"Model '{settings.OPENAI_MODEL}' not found or not available on your account.",
        ) from e
    except APIStatusError as e:
        raise MatchingError(*_humanize_openai_error(e)) from e

    _log_token_usage("OpenAI", len(listings_batch), response.usage.prompt_tokens, response.usage.completion_tokens)
    _log_raw_output("OpenAI", listings_batch, response.choices[0].message.content)
    return _parse_response_text(response.choices[0].message.content)


def _compute_deterministic_scores(raw_items: list[dict]) -> list[dict]:
    """
    Converts the model's per-listing requirements-met breakdown into an
    actual 0-100 score computed by OUR code, not trusted directly from the
    model.

    Why: earlier versions asked the model to compute its own score under a
    ceiling rule ("cap at 55 if missing 1 of N requirements"). Testing
    repeatedly showed the model's REASON TEXT would correctly identify a
    missed requirement ("doesn't mention Caltrain") while the SCORE ignored
    its own stated ceiling and came back 100 anyway — the model wasn't
    reliably doing that arithmetic itself. Asking it to output simple
    per-requirement booleans (a much more constrained, reliable judgment)
    and doing the actual score = round(100 * met/total) math in Python
    removes that failure mode entirely, since the model can no longer
    "forget" to apply the cap — there's no cap for it to apply or skip.
    """
    results = []
    for item in raw_items:
        reqs = item.get("requirements", [])
        if reqs:
            total = len(reqs)
            met = sum(1 for r in reqs if r.get("met"))
            score = round(100 * met / total)
        else:
            # Model didn't break preferences into requirements at all
            # (shouldn't normally happen given the prompt, but don't crash
            # if it does) — neutral fallback rather than silently dropping
            # this listing from results entirely. total=0 signals "unknown"
            # to callers doing count-based filtering below.
            score = 50
            total = 0
            met = 0
        results.append({
            "mls_id": item.get("mls_id"),
            "score": score,
            "reason": item.get("reason", ""),
            "requirements_total": total,
            "requirements_met": met,
        })
    return results


def score_batch(user_preferences: str, listings_batch: list[dict], ai_provider: str = None, on_retry=None) -> list[dict]:
    provider = ai_provider or settings.AI_PROVIDER
    if provider not in VALID_AI_PROVIDERS:
        raise ValueError(f"ai_provider must be one of {VALID_AI_PROVIDERS}, got '{provider}'")
    if provider == "openai":
        raw = _score_batch_openai(user_preferences, listings_batch, on_retry)
    else:
        raw = _score_batch_anthropic(user_preferences, listings_batch, on_retry)
    return _compute_deterministic_scores(raw)


def rank_listings(user_preferences: str, listings: list[dict], ai_provider: str = None) -> list[dict]:
    """Batches, scores, merges, filters by threshold, sorts best-first.
    Synchronous, blocking, no cancellation — kept for the CLI script and
    anything that just wants a single call/response. The API's /match/start
    endpoint uses the job-based version below instead, which supports
    real mid-search cancellation.

    Batches run concurrently (up to settings.MAX_CONCURRENT_BATCHES at once)
    rather than one at a time — for a large search this is the single
    biggest lever on total wall-clock time, since network/API latency per
    batch otherwise adds up linearly.

    Every ranked listing carries requirements_total/requirements_met so the
    frontend can show "2/3 requirements met" transparently and separate full
    matches from partial ones — deliberately NOT a user-configurable filter
    (asking someone to pre-declare which of their own stated requirements
    they're willing to have ignored, before seeing any results, doesn't
    make sense as a control).
    """
    batches = [listings[i:i + settings.BATCH_SIZE] for i in range(0, len(listings), settings.BATCH_SIZE)]
    scores_by_id = {}

    with ThreadPoolExecutor(max_workers=settings.MAX_CONCURRENT_BATCHES) as executor:
        futures = [executor.submit(score_batch, user_preferences, batch, ai_provider) for batch in batches]
        for future in as_completed(futures):
            for r in future.result():
                scores_by_id[str(r["mls_id"])] = r  # str() — model may return ids as strings even when source has ints

    ranked = []
    for listing in listings:
        result = scores_by_id.get(str(listing["mls_id"]))
        if not result:
            continue
        if result["score"] < settings.SCORE_THRESHOLD:
            continue
        ranked.append({
            **listing,
            "match_score": result["score"],
            "match_reason": result["reason"],
            "requirements_total": result.get("requirements_total", 0),
            "requirements_met": result.get("requirements_met", 0),
        })

    ranked.sort(key=lambda x: x["match_score"], reverse=True)
    return ranked


# ---------------------------------------------------------------------------
# Job-based matching with real mid-search cancellation.
#
# Each search runs in a background thread. Between batches (not mid-batch —
# an already-sent API call can't be recalled), the thread checks a
# threading.Event. If it's set, the loop stops immediately: no further
# batches get sent, and whatever was already scored is returned as partial
# results. Jobs live in an in-memory dict — fine for a single-user local
# app; a real multi-user deployment would use Redis or a proper task queue
# (Celery, RQ) instead, since this dict is lost on server restart and
# doesn't work across multiple server processes.
# ---------------------------------------------------------------------------

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _build_batches(listings: list[dict]) -> list[list[dict]]:
    """Send a size-1 'warm-up' batch first, then the rest at normal
    BATCH_SIZE. A single-listing batch has far less output to generate
    than a full one, so it tends to come back fastest of everything in
    flight — the goal is genuinely showing the user *something* as soon
    as the first API response lands, not a blank screen with only a
    progress count until a full batch completes. Only makes sense with
    2+ listings; with exactly 1, there's nothing left to split further.

    Pulled out as its own function specifically so start_match_job (which
    needs the total batch COUNT immediately, before the background thread
    even runs) and _run_job (which needs the actual batch CONTENTS) always
    agree — they used to compute this separately, which drifted apart the
    moment this warm-up logic was added to one but not the other, showing
    a total_batches count in the UI that didn't match what was actually
    running."""
    if len(listings) > 1:
        return [[listings[0]]] + [
            listings[i:i + settings.BATCH_SIZE] for i in range(1, len(listings), settings.BATCH_SIZE)
        ]
    return [listings] if listings else []


def start_match_job(user_preferences: str, listings: list[dict], ai_provider: str = None) -> str:
    job_id = str(uuid.uuid4())
    total_batches = len(_build_batches(listings))

    job = {
        "status": "running",       # running | done | cancelled | error
        "results": [],
        "error": None,
        "cancel_event": threading.Event(),
        "total_batches": total_batches,
        "completed_batches": 0,
        "in_flight_count": 0,      # how many batches are actively running right now, at this instant
        "retry_count": 0,          # cumulative retries triggered so far across the whole job
        "job_lock": threading.Lock(),  # protects retry_count from concurrent batch threads
    }
    with _jobs_lock:
        _jobs[job_id] = job

    thread = threading.Thread(target=_run_job, args=(job_id, user_preferences, listings, ai_provider), daemon=True)
    thread.start()
    return job_id


def _run_job(job_id: str, user_preferences: str, listings: list[dict], ai_provider: str = None):
    """
    Runs up to settings.MAX_CONCURRENT_BATCHES batches at once instead of
    one-at-a-time — the sliding-window pattern below submits a fresh batch
    each time one finishes, keeping that many in flight simultaneously.
    Cancellation still works the same as before: once cancel_event is set,
    no NEW batches are submitted, but whichever are already in flight (up to
    MAX_CONCURRENT_BATCHES of them) are allowed to finish rather than
    abandoned mid-request.
    """
    job = _jobs[job_id]
    cancel_event = job["cancel_event"]
    scores_by_id = {}

    # See _build_batches' docstring for why this is a shared helper, not
    # computed inline here — it used to be, and drifted out of sync with
    # start_match_job's separate calculation of the same thing.
    batches = _build_batches(listings)
    batch_iter = iter(batches)

    def on_retry(attempt, delay):
        with job["job_lock"]:
            job["retry_count"] += 1

    def update_results_so_far():
        """Rebuilds job['results'] from whatever's been scored so far and
        writes it back — called after EVERY batch completes, not just once
        at the very end. This is what lets the frontend show real results
        progressively while a search is still running, instead of a blank
        results area until the whole thing finishes. Cheap to do on every
        batch: at most 500 listings, a plain filter+sort, negligible cost
        next to the multi-second network calls this runs alongside."""
        ranked = []
        for listing in listings:
            result = scores_by_id.get(str(listing["mls_id"]))
            if not result:
                continue
            if result["score"] < settings.SCORE_THRESHOLD:
                continue
            ranked.append({
                **listing,
                "match_score": result["score"],
                "match_reason": result["reason"],
                "requirements_total": result.get("requirements_total", 0),
                "requirements_met": result.get("requirements_met", 0),
            })
        ranked.sort(key=lambda x: x["match_score"], reverse=True)
        job["results"] = ranked

    try:
        with ThreadPoolExecutor(max_workers=settings.MAX_CONCURRENT_BATCHES) as executor:
            in_flight = {}  # future -> True, just used as a set with quick membership ops

            def submit_next():
                if cancel_event.is_set():
                    return
                batch = next(batch_iter, None)
                if batch is not None:
                    in_flight[executor.submit(score_batch, user_preferences, batch, ai_provider, on_retry)] = True
                job["in_flight_count"] = len(in_flight)

            for _ in range(settings.MAX_CONCURRENT_BATCHES):
                submit_next()

            while in_flight:
                done, _ = wait(in_flight.keys(), return_when=FIRST_COMPLETED)
                for future in done:
                    for r in future.result():
                        scores_by_id[str(r["mls_id"])] = r
                    job["completed_batches"] += 1
                    del in_flight[future]
                    update_results_so_far()  # progressive results — every batch, not just the last one
                    submit_next()  # keep the window full, unless cancelled

        job["status"] = "cancelled" if cancel_event.is_set() else "done"
    except MatchingError as e:
        # The expected, categorized case — log full technical detail
        # server-side only, store just the clean client-facing message in
        # job["error"] (this is what the frontend actually displays via
        # polling — see routers/match.py's get_match_status).
        print(f"[job {job_id} error] {e.technical_detail}")
        job["status"] = "error"
        job["error"] = e.client_message
    except Exception as e:
        # Deliberately broad, not just MatchingError — this is the last
        # line of defense for a background thread. Anything raised in here
        # that we didn't anticipate MUST still flip the job to "error"
        # status (see the long-standing comment history on this exact
        # line for why: an uncaught exception type here used to silently
        # kill the thread while job["status"] stayed "running" forever).
        # The client still gets a clean, generic message — never str(e) —
        # consistent with MatchingError above; the real detail goes to
        # server logs only.
        print(f"[job {job_id} error] unexpected exception type {type(e).__name__}: {e}")
        job["status"] = "error"
        job["error"] = _CLIENT_MSG_UNKNOWN


def cancel_job(job_id: str) -> bool:
    """Signals the background thread to stop before its next batch. Returns
    False if the job doesn't exist (already cleaned up, or bad id)."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return False
    job["cancel_event"].set()
    return True


def get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        return _jobs.get(job_id)
