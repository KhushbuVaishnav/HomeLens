"""
app/config.py

Centralized configuration, driven by environment variables (loaded from
.env via python-dotenv). This replaces the old pattern of flipping
USE_SAMPLE_DATA / USE_REALISTIC_DATA / USE_GENERATED_DATA booleans directly
in fetch_listings.py — in a real project you don't want to edit source code
to change which data source or AI provider is active, especially once this
runs anywhere besides your laptop (staging, CI, a teammate's machine).

Set these in your .env file:
    DATA_SOURCE=generated        # one of: live, realistic, generated
    AI_PROVIDER=anthropic        # one of: anthropic, openai
    SCORE_THRESHOLD=60           # 0-100
    BATCH_SIZE=8
    CORS_ALLOW_ORIGINS=*         # comma-separated in production, e.g. https://yourapp.com
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent  # .../app
DATA_DIR = BASE_DIR / "data"

# Single source of truth for valid option values — imported by services and
# routers that need to validate against these, instead of each defining its
# own copy (which is how these can silently drift out of sync over time).
VALID_DATA_SOURCES = ("live", "realistic", "generated")
VALID_AI_PROVIDERS = ("anthropic", "openai")


class Settings:
    # --- Data source ---
    # "live"      -> real SimplyRETS sandbox API
    # "realistic" -> app/data/realistic_listings.json (14 hand-written listings)
    # "generated" -> app/data/generated_listings.json (large generated set)
    DATA_SOURCE: str = os.environ.get("DATA_SOURCE", "generated").lower()

    REALISTIC_DATA_PATH: Path = DATA_DIR / "realistic_listings.json"
    GENERATED_DATA_PATH: Path = DATA_DIR / "generated_listings.json"
    SCHOOLS_PATH: Path = DATA_DIR / "schools.json"  # kept as the source data — see seed_schools_db.py
    SCHOOLS_DB_PATH: Path = DATA_DIR / "schools.db"  # what schools_service.py actually queries

    SIMPLYRETS_BASE_URL: str = "https://api.simplyrets.com/properties"
    SIMPLYRETS_AUTH: tuple = ("simplyrets", "simplyrets")  # public sandbox creds

    # --- AI provider ---
    AI_PROVIDER: str = os.environ.get("AI_PROVIDER", "anthropic").lower()
    ANTHROPIC_API_KEY: str | None = os.environ.get("ANTHROPIC_API_KEY")
    OPENAI_API_KEY: str | None = os.environ.get("OPENAI_API_KEY")
    ANTHROPIC_MODEL: str = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")
    OPENAI_REASONING_EFFORT: str | None = os.environ.get("OPENAI_REASONING_EFFORT") or None
    # Optional: "none" | "low" | "medium" | "high" | "xhigh". Unset by default,
    # meaning we don't send this param at all and the model uses its own
    # default reasoning behavior. Per OpenAI's docs, temperature is only
    # accepted alongside reasoning_effort="none" specifically — see
    # _score_batch_openai for how that's handled.

    # --- Matching behavior ---
    BATCH_SIZE: int = int(os.environ.get("BATCH_SIZE", 8))
    SCORE_THRESHOLD: int = int(os.environ.get("SCORE_THRESHOLD", 60))
    TEMPERATURE: float = float(os.environ.get("TEMPERATURE", 0.2))
    MAX_TOKENS: int = int(os.environ.get("MAX_TOKENS", 2000))
    REQUEST_TIMEOUT_SECONDS: int = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", 60))
    # Without this, a single hung network connection to Claude/OpenAI (which
    # can genuinely happen — dropped connections, proxy interference, a
    # stalled server-side response) would block that batch's thread forever.
    # Since Cancel only stops NEW batches from being submitted — it can't
    # recall one already in flight — a permanently-stuck request would
    # freeze the entire job with no way to stop it, even after cancelling.
    MAX_CONCURRENT_BATCHES: int = int(os.environ.get("MAX_CONCURRENT_BATCHES", 4))
    # How many batches run at once instead of one-at-a-time. Higher = faster
    # wall-clock time for a big search. Don't guess this — check the
    # "[<provider> rate limits] requests: X/Y remaining" lines this app
    # prints to the terminal on every call. If X stays close to Y (lots of
    # headroom left) with zero retry/429 lines, you likely have room to
    # raise this well past the conservative default of 4 — go up gradually
    # (e.g. try 8, confirm it's still clean) rather than jumping to an
    # extreme value, since diminishing returns and thread overhead mean
    # more isn't always proportionally faster.

    # --- API / server ---
    CORS_ALLOW_ORIGINS: list = os.environ.get("CORS_ALLOW_ORIGINS", "*").split(",")
    DEFAULT_FETCH_LIMIT: int = int(os.environ.get("DEFAULT_FETCH_LIMIT", 1000))

    def validate(self):
        """Call at startup to fail fast on misconfiguration instead of erroring mid-request."""
        if self.DATA_SOURCE not in VALID_DATA_SOURCES:
            raise ValueError(f"DATA_SOURCE must be one of {VALID_DATA_SOURCES}, got '{self.DATA_SOURCE}'")

        if self.AI_PROVIDER not in VALID_AI_PROVIDERS:
            raise ValueError(f"AI_PROVIDER must be one of {VALID_AI_PROVIDERS}, got '{self.AI_PROVIDER}'")

        # Hard failure: the DEFAULT provider must have its key set, since
        # every request falls back to this unless the frontend's "Matched
        # by" dropdown explicitly overrides it.
        if self.AI_PROVIDER == "anthropic" and not self.ANTHROPIC_API_KEY:
            raise ValueError("AI_PROVIDER is 'anthropic' but ANTHROPIC_API_KEY is not set in .env")
        if self.AI_PROVIDER == "openai" and not self.OPENAI_API_KEY:
            raise ValueError("AI_PROVIDER is 'openai' but OPENAI_API_KEY is not set in .env")

        # Soft warning, not a hard failure: the frontend's dropdown lets
        # someone pick EITHER provider for a single search, regardless of
        # which one is the .env default. If the non-default provider's key
        # is missing, that's fine if you never plan to use it — but you
        # should know now, not discover it only when a search using that
        # dropdown option fails with an auth error mid-request.
        if not self.ANTHROPIC_API_KEY:
            print("[config warning] ANTHROPIC_API_KEY is not set — selecting 'Claude' in the "
                  "frontend's 'Matched by' dropdown will fail until it's added to .env.")
        if not self.OPENAI_API_KEY:
            print("[config warning] OPENAI_API_KEY is not set — selecting 'OpenAI' in the "
                  "frontend's 'Matched by' dropdown will fail until it's added to .env.")


settings = Settings()
