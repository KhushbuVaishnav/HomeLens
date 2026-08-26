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
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from langsmith import traceable, get_current_run_tree
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
    Retries fn() on rate-limit errors AND connection errors, with
    exponential backoff (2s, 4s, 8s...). Rate limits (too many
    requests/tokens per minute) are expected, temporary conditions —
    especially now that batches run concurrently (MAX_CONCURRENT_BATCHES)
    — not a real failure, so retrying briefly is the standard approach
    rather than immediately surfacing an error to the user. Connection
    errors (the request never reached the provider's servers at all —
    distinct from a slow response, which is APITimeoutError, or a
    rejected request, which is a 4xx) are just as often transient as a
    rate limit — a brief network blip, or a burst of new connections
    opening at once — so they get the same retry treatment instead of
    failing the whole batch on the first hiccup. Any OTHER exception
    (auth, not-found, etc.) is still raised immediately, since those
    won't resolve by waiting.

    Shared by all three providers — Vertex's google-genai SDK doesn't have
    a dedicated rate-limit exception class the way anthropic/openai do,
    so its 429s are recognized by status code instead (see below), not by
    exception type. Connection-error retry is currently anthropic/openai
    only — google-genai's transport-level connection failures don't
    consistently surface as its APIError class the way status-coded
    failures do, so they're not classified here yet.

    on_retry(attempt, delay, reason), if given, is called each time a retry
    is about to happen — reason is "rate limit" or "connection error".
    Used by the job runner to surface "this is retrying, and why" to the
    frontend via the poll endpoint, not just as a terminal print.
    """
    import anthropic
    import openai
    try:
        from google.genai import errors as genai_errors
    except ImportError:
        # google-genai is an optional dependency — only needed if
        # AI_PROVIDER=vertex is actually used. Someone who only ever uses
        # Anthropic/OpenAI shouldn't need it installed at all; this
        # function is shared by all three providers, so it can't assume
        # it's present.
        genai_errors = None

    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            is_retryable = isinstance(e, (
                anthropic.RateLimitError, openai.RateLimitError,
                anthropic.APIConnectionError, openai.APIConnectionError,
            ))
            if not is_retryable and genai_errors is not None:
                # google-genai doesn't have its own dedicated RateLimitError
                # class the way anthropic/openai do — a 429 surfaces as the
                # same APIError every other status code does, distinguished
                # only by its .code attribute.
                is_retryable = isinstance(e, genai_errors.APIError) and getattr(e, "code", None) == 429
            if not is_retryable or attempt == max_attempts - 1:
                raise
            delay = base_delay * (2 ** attempt)
            reason = "connection error" if isinstance(e, (anthropic.APIConnectionError, openai.APIConnectionError)) else "rate limit"
            print(f"[{reason}] Hit a retryable error, retrying in {delay:.0f}s (attempt {attempt + 1}/{max_attempts})...")
            if on_retry:
                on_retry(attempt + 1, delay, reason)
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


@traceable(name="score_batch_anthropic")
def _score_batch_anthropic(user_preferences: str, listings_batch: list[dict], on_retry=None) -> list[dict]:
    import anthropic

    if not settings.ANTHROPIC_API_KEY:
        raise MatchingError(
            _CLIENT_MSG_CONFIG,
            "ANTHROPIC_API_KEY is not set — required to use Claude as the AI provider. Add it to .env, or select a different provider.",
        )

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=settings.REQUEST_TIMEOUT_SECONDS)
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
            # cache_control marks SYSTEM_PROMPT as a reusable prefix — unlike
            # OpenAI/Gemini, Anthropic only caches a block you explicitly
            # flag this way, nothing is automatic. "ephemeral" is a 5-minute
            # TTL, refreshed on every cache hit, which is the only TTL
            # Anthropic currently offers. Below the model's minimum
            # cacheable length (1024 tokens for Sonnet/Opus, 2048 for
            # Haiku), this is silently a no-op — no error, cache_creation_
            # input_tokens in the usage response just comes back 0. Verify
            # empirically via the "cache read"/"cache write" figures this
            # file now logs, don't assume it's working from the code alone.
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_message}],
        )
        _log_rate_limit_headers("Anthropic", raw.headers)
        return raw.parse()

    try:
        _call_start = time.perf_counter()
        response = _retry_with_backoff(call, on_retry=on_retry)
        _elapsed = time.perf_counter() - _call_start
    except anthropic.RateLimitError as e:
        raise MatchingError(
            _CLIENT_MSG_TRANSIENT,
            "Anthropic rate limit hit repeatedly even after retrying — you're sending requests "
            "faster than your account's current limit allows. Try lowering MAX_CONCURRENT_BATCHES "
            "in .env, or check your rate limits in the Anthropic Console.",
        ) from e
    except anthropic.APITimeoutError as e:
        raise MatchingError(
            _CLIENT_MSG_TRANSIENT,
            f"Anthropic request timed out after {settings.REQUEST_TIMEOUT_SECONDS}s with no response — "
            "likely a network issue or a stalled connection, not something wrong with your search itself. "
            "Try again, or increase REQUEST_TIMEOUT_SECONDS in .env if this keeps happening on large batches.",
        ) from e
    except anthropic.APIConnectionError as e:
        raise MatchingError(
            _CLIENT_MSG_TRANSIENT,
            "Couldn't establish a connection to Anthropic's API — check your network connection.",
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

    _log_token_usage(
        "Anthropic", len(listings_batch), response.usage.input_tokens, response.usage.output_tokens, _elapsed,
        cached_tokens=response.usage.cache_read_input_tokens, cache_write_tokens=response.usage.cache_creation_input_tokens,
    )
    _log_raw_output("Anthropic", listings_batch, response.content[0].text)
    return _parse_response_text(response.content[0].text)


def _log_token_usage(
    provider: str, batch_size: int, input_tokens: int, output_tokens: int, elapsed_seconds: float,
    cached_tokens: int | None = None, cache_write_tokens: int | None = None,
) -> None:
    """Prints EXACT token usage straight from the API response's own usage
    field — reliable even under concurrency, unlike trying to infer usage
    from the shared rate-limit 'remaining' headers (those reflect multiple
    threads' calls interleaved against a bucket that's also continuously
    refilling, so they can't be cleanly subtracted to get a single call's
    real cost).

    elapsed_seconds is measured around the WHOLE _retry_with_backoff(call)
    span, not a single raw API attempt — if this batch hit a retry, that
    backoff sleep time (2s, 4s, 8s...) is included in what's printed here.
    Deliberately measured this way: "how long did it actually take to get
    a usable result for this batch" is the number that matters for a real
    P95 of what a search experiences, not an idealized single-attempt
    number that hides how often retries actually happen in practice.

    cached_tokens / cache_write_tokens come straight from each provider's
    own usage payload (not inferred) — the only reliable way to confirm
    prompt caching is actually landing on a given call, rather than
    assuming it from the code alone. Omitted from the line entirely when
    a provider doesn't report them (None), rather than printing a
    misleading 0.

    Also attaches these same numbers as metadata on the current LangSmith
    run, if tracing is active (get_current_run_tree() returns None when
    it isn't — see the @traceable docstring on each _score_batch_* caller
    for why this is always safe to call unconditionally). This is the one
    place all three providers' token/cache numbers already funnel through,
    so it's the cheapest hook point to also surface them in LangSmith's
    UI instead of only ever being visible in this print() line."""
    cache_part = ""
    if cached_tokens is not None:
        cache_part += f", cache read: {cached_tokens} tokens"
    if cache_write_tokens is not None:
        cache_part += f", cache write: {cache_write_tokens} tokens"
    print(f"[{provider} usage] batch of {batch_size} listings — "
          f"input: {input_tokens} tokens, output: {output_tokens} tokens, "
          f"total: {input_tokens + output_tokens} tokens{cache_part} — "
          f"latency: {elapsed_seconds * 1000:.0f}ms")

    run = get_current_run_tree()
    if run:
        run.metadata.update({
            "provider": provider,
            "batch_size": batch_size,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "cache_write_tokens": cache_write_tokens,
            "elapsed_ms": round(elapsed_seconds * 1000),
        })


def _log_raw_output(provider: str, listings_batch: list[dict], raw_text: str) -> None:
    """Prints the model's raw, unparsed response text — server-side only,
    same principle as every other technical log in this file (never sent
    to the browser). Useful for actually seeing what the model said,
    e.g. while tuning the system prompt or debugging a parse failure.
    Includes the batch's mls_ids so it's identifiable in a busy,
    concurrent log with several batches' output interleaved.

    Gated behind DEBUG_MODE (see config.py) — this is a full model
    response per batch, easily a multi-KB dump, off by default."""
    if not settings.DEBUG_MODE:
        return
    ids = [str(l.get("mls_id")) for l in listings_batch]
    print(f"[{provider} raw output] batch mls_ids={ids}:\n{raw_text}\n{'-' * 60}")


def _log_raw_input(provider: str, listings_batch: list[dict], user_message: str) -> None:
    """Same idea as _log_raw_output, but for what we actually SEND —
    the buyer's preferences plus the exact JSON payload built by
    _build_listing_payload() for this batch (SYSTEM_PROMPT itself isn't
    repeated here since it's identical on every call; this is the part
    that varies per batch and is what actually matters for debugging,
    e.g. confirming a field like year_built genuinely made it into what
    the model received, not just that it exists in our own data).

    Gated behind DEBUG_MODE (see config.py) — same reasoning as
    _log_raw_output, this is a full JSON payload per batch."""
    if not settings.DEBUG_MODE:
        return
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


@traceable(name="score_batch_openai")
def _score_batch_openai(user_preferences: str, listings_batch: list[dict], on_retry=None) -> list[dict]:
    import openai
    from openai import OpenAI, AuthenticationError, NotFoundError, APIStatusError, APITimeoutError, APIConnectionError

    if not settings.OPENAI_API_KEY:
        raise MatchingError(
            _CLIENT_MSG_CONFIG,
            "OPENAI_API_KEY is not set — required to use OpenAI as the AI provider. Add it to .env, or select a different provider.",
        )

    client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=settings.REQUEST_TIMEOUT_SECONDS)
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
        _call_start = time.perf_counter()
        response = _retry_with_backoff(call, on_retry=on_retry)
        _elapsed = time.perf_counter() - _call_start
    except openai.RateLimitError as e:
        raise MatchingError(
            _CLIENT_MSG_TRANSIENT,
            "OpenAI rate limit hit repeatedly even after retrying — you're sending requests faster "
            "than your account's current limit allows. Try lowering MAX_CONCURRENT_BATCHES in .env, "
            "or check your rate limits at platform.openai.com.",
        ) from e
    except APITimeoutError as e:
        raise MatchingError(
            _CLIENT_MSG_TRANSIENT,
            f"OpenAI request timed out after {settings.REQUEST_TIMEOUT_SECONDS}s with no response — "
            "likely a network issue or a stalled connection, not something wrong with your search itself. "
            "Try again, or increase REQUEST_TIMEOUT_SECONDS in .env if this keeps happening on large batches.",
        ) from e
    except APIConnectionError as e:
        raise MatchingError(
            _CLIENT_MSG_TRANSIENT,
            "Couldn't establish a connection to OpenAI's API — check your network connection.",
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

    # OpenAI caches automatically server-side once a request's shared prefix
    # exceeds 1024 tokens — nothing to opt into in the request itself, so
    # this is purely a read of whether that kicked in for this particular
    # call, not something the code below causes.
    cached = getattr(response.usage, "prompt_tokens_details", None)
    cached_tokens = getattr(cached, "cached_tokens", None) if cached else None
    _log_token_usage(
        "OpenAI", len(listings_batch), response.usage.prompt_tokens, response.usage.completion_tokens, _elapsed,
        cached_tokens=cached_tokens,
    )
    _log_raw_output("OpenAI", listings_batch, response.choices[0].message.content)
    return _parse_response_text(response.choices[0].message.content)


@traceable(name="score_batch_vertex")
def _score_batch_vertex(user_preferences: str, listings_batch: list[dict], on_retry=None) -> list[dict]:
    """Vertex AI's Gemini, via the google-genai SDK's Vertex mode
    (client = genai.Client(vertexai=True, ...)) — the current, non-deprecated
    way to call it. The older `vertexai.generative_models.GenerativeModel`
    class this project's own earlier drafts might reference is deprecated
    as of mid-2025 and being fully removed; deliberately not used here.

    Authentication is the real structural difference from the other two
    providers: no API key checked or sent anywhere. Vertex AI authenticates
    via Google Cloud's Application Default Credentials — locally, whatever
    `gcloud auth application-default login` set up; on Cloud Run, the
    service's own identity, granted the "Vertex AI User" IAM role. If ADC
    isn't set up at all, the SDK's own error surfaces clearly in the
    exception handling below rather than this code trying to detect that
    case itself beforehand.
    """
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types as genai_types

    if not settings.GCP_PROJECT_ID:
        raise MatchingError(
            _CLIENT_MSG_CONFIG,
            "GCP_PROJECT_ID is not set — required to use Vertex AI (Gemini) as the AI provider. "
            "Add it to .env, or select a different provider.",
        )

    client = genai.Client(vertexai=True, project=settings.GCP_PROJECT_ID, location=settings.GCP_REGION)
    user_message = f"Buyer wants: {user_preferences}\n\nListings:\n{json.dumps(_build_listing_payload(listings_batch), indent=2)}"
    _log_raw_input("Vertex", listings_batch, user_message)

    def call():
        return client.models.generate_content(
            model=settings.VERTEX_MODEL,
            contents=f"{SYSTEM_PROMPT}\n\n{user_message}",
            # Gemini's SDK doesn't expose a separate system/user role split
            # the same way Anthropic/OpenAI do in this call shape — folded
            # into one prompt string instead, same content either way.
            config=genai_types.GenerateContentConfig(
                temperature=settings.TEMPERATURE,
                # Matches Claude's temperature setting — same TEMPERATURE
                # config value, same reasoning: lower temperature for more
                # consistent, less creative scoring judgments. Previously
                # unset here, meaning Gemini was silently running on
                # whatever its own SDK default is, not the value actually
                # configured for this app.
                response_mime_type="application/json",
                # Claude/OpenAI reliably return clean JSON from the prompt
                # instruction alone ("Respond with ONLY a JSON array").
                # Gemini needs this told explicitly, not just asked in the
                # prompt — without it, Gemini can wrap its answer in
                # explanatory text even when instructed not to, which
                # silently breaks _parse_response_text (it just returns []
                # and prints a parse-failure warning), meaning every
                # listing in that batch gets skipped with no error raised
                # anywhere — exactly the "runs fine, zero results" failure
                # mode this was actually causing.
            ),
        )

    try:
        _call_start = time.perf_counter()
        response = _retry_with_backoff(call, on_retry=on_retry)
        _elapsed = time.perf_counter() - _call_start
    except genai_errors.APIError as e:
        code = getattr(e, "code", None)
        message = getattr(e, "message", str(e))
        if code == 429:
            raise MatchingError(
                _CLIENT_MSG_TRANSIENT,
                "Vertex AI rate limit hit repeatedly even after retrying — you're sending requests "
                "faster than your project's current quota allows. Try lowering MAX_CONCURRENT_BATCHES "
                "in .env, or check your quotas in the Google Cloud Console.",
            ) from e
        if code in (401, 403):
            raise MatchingError(
                _CLIENT_MSG_CONFIG,
                f"Vertex AI authentication/permission failed ({code}): {message} — locally, run "
                "`gcloud auth application-default login`; on Cloud Run, confirm the service's "
                "identity has the 'Vertex AI User' IAM role on GCP_PROJECT_ID.",
            ) from e
        if code == 404:
            raise MatchingError(
                _CLIENT_MSG_CONFIG,
                f"Model '{settings.VERTEX_MODEL}' not found or not available in region '{settings.GCP_REGION}' "
                f"for project '{settings.GCP_PROJECT_ID}'.",
            ) from e
        raise MatchingError(_CLIENT_MSG_UNKNOWN, f"Vertex AI API error ({code}): {message}") from e

    usage = getattr(response, "usage_metadata", None)
    input_tokens = getattr(usage, "prompt_token_count", 0) if usage else 0
    output_tokens = getattr(usage, "candidates_token_count", 0) if usage else 0
    # Gemini 2.5 models apply implicit prefix caching automatically, same
    # as OpenAI — no request change needed, just reading whether it hit.
    cached_tokens = getattr(usage, "cached_content_token_count", None) if usage else None
    _log_token_usage("Vertex", len(listings_batch), input_tokens, output_tokens, _elapsed, cached_tokens=cached_tokens)
    _log_raw_output("Vertex", listings_batch, response.text)
    return _parse_response_text(response.text)


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
            "requirements": reqs,  # the itemized [{"text": ..., "met": bool}, ...] breakdown
            # itself, not just its counts — added so callers that need the
            # per-requirement detail (llm_judge.py's judge payload) can get
            # it directly from score_batch's own return value, instead of
            # re-deriving or re-requesting it. Purely additive: nothing
            # downstream reads this key today (_merge_and_rank only pulls
            # score/reason/requirements_total/requirements_met), so this
            # can't change any existing behavior.
        })
    return results


@traceable(name="score_batch")
# Off by default, zero network activity, and safe to leave on this
# unconditionally — @traceable only actually does anything when
# LANGSMITH_TRACING=true and a real LANGSMITH_API_KEY are set (see
# .env.example). The three _score_batch_<provider> functions below are
# also @traceable, so with tracing on you get one parent "score_batch"
# run in LangSmith with the actual provider call nested underneath it —
# traceable tracks that nesting itself via contextvars, nothing here has
# to wire the parent/child relationship up manually.
def score_batch(user_preferences: str, listings_batch: list[dict], ai_provider: str = None, on_retry=None) -> list[dict]:
    provider = ai_provider or settings.AI_PROVIDER
    if provider not in VALID_AI_PROVIDERS:
        raise ValueError(f"ai_provider must be one of {VALID_AI_PROVIDERS}, got '{provider}'")
    if provider == "openai":
        raw = _score_batch_openai(user_preferences, listings_batch, on_retry)
    elif provider == "vertex":
        raw = _score_batch_vertex(user_preferences, listings_batch, on_retry)
    else:
        raw = _score_batch_anthropic(user_preferences, listings_batch, on_retry)
    return _compute_deterministic_scores(raw)


def _merge_and_rank(listings: list[dict], scores_by_id: dict) -> list[dict]:
    """Merges scored results into their listings, drops anything below
    SCORE_THRESHOLD (or never scored at all), and sorts best-first.

    Shared by rank_listings() (single blocking call) and _run_job's
    update_results_so_far() (progressive job results) so the two paths
    can't drift apart — they used to each implement this merge separately,
    which is exactly the kind of duplication that caused _build_batches to
    drift out of sync in the past (see that function's docstring)."""
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

    return _merge_and_rank(listings, scores_by_id)


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
        "failed_batches": 0,       # batches that errored out (e.g. timeout) — the search continues without them, doesn't abort
        "in_flight_count": 0,      # how many batches are actively running right now, at this instant
        "retry_count": 0,          # cumulative retries triggered so far across the whole job, all reasons combined
        "retry_reasons": {},       # {"rate limit": N, "connection error": N} -- lets the frontend say WHY, not just how many.
        # Kept alongside retry_count (not replacing it) so nothing that
        # already reads the plain total needs to change to keep working.
        "job_lock": threading.Lock(),  # protects retry_count/retry_reasons from concurrent batch threads
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

    def on_retry(attempt, delay, reason):
        with job["job_lock"]:
            job["retry_count"] += 1
            job["retry_reasons"][reason] = job["retry_reasons"].get(reason, 0) + 1

    def update_results_so_far():
        """Rebuilds job['results'] from whatever's been scored so far and
        writes it back — called after EVERY batch completes, not just once
        at the very end. This is what lets the frontend show real results
        progressively while a search is still running, instead of a blank
        results area until the whole thing finishes. Cheap to do on every
        batch: at most 500 listings, a plain filter+sort, negligible cost
        next to the multi-second network calls this runs alongside."""
        job["results"] = _merge_and_rank(listings, scores_by_id)

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
                    try:
                        for r in future.result():
                            scores_by_id[str(r["mls_id"])] = r
                    except MatchingError as e:
                        # One batch failing (e.g. a timeout) shouldn't sink
                        # the entire search — log it, count it, and keep
                        # going with everything else. The listings in this
                        # specific batch just won't appear in the results;
                        # everything else still completes normally.
                        print(f"[job {job_id} batch failed] {e.technical_detail}")
                        job["failed_batches"] += 1
                    except Exception as e:
                        # Same reasoning as MatchingError above, but for a
                        # genuinely unanticipated exception type — still
                        # shouldn't sink the whole search over one batch.
                        print(f"[job {job_id} batch failed] unexpected exception type {type(e).__name__}: {e}")
                        job["failed_batches"] += 1
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
