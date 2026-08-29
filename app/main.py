"""
app/main.py

FastAPI app assembly. Run with:
    uvicorn app.main:app --reload
from the project root (not from inside app/).

Then open http://127.0.0.1:8000/docs for interactive Swagger docs.
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings, VALID_DATA_SOURCES, VALID_AI_PROVIDERS
from app.routers import listings, match, vector_search, smart_search

settings.validate()  # fail fast on misconfiguration (bad DATA_SOURCE, missing API key, etc.)


def _log_startup_diagnostics():
    """One-time diagnostic printed when the container boots — added
    specifically to debug OpenAI connection failures that only happen on
    this deployment (vertex-experiment) and not on an otherwise identical
    deployment (main) running the same matching_service.py code. Since the
    actual API-calling code is provably identical between the two, the
    remaining candidates are things OUTSIDE application code: a shared
    dependency (httpx, used by both the openai and anthropic SDKs)
    resolving to a different version because this branch's requirements.txt
    has one extra package (google-genai) that main's doesn't, or a proxy
    environment variable present in one container and not the other.
    Confirmed real mechanism, not speculation: per google-genai's own docs
    (googleapis.github.io/python-genai), "Both httpx and aiohttp libraries
    use urllib.request.getproxies from environment variables" — httpx
    (OpenAI's transport too) picks up HTTP_PROXY/HTTPS_PROXY automatically
    unless explicitly disabled. If a proxy var is present here and absent
    on main, or vice versa, that alone could fully explain
    provider-specific, environment-specific connection failures with zero
    application code being at fault.
    Not gated behind DEBUG_MODE — this runs once per container start, not
    per-request, so the cost is negligible and the value (comparing this
    against the other deployment's logs) is immediate.
    """
    try:
        import httpx
        httpx_version = httpx.__version__
    except ImportError:
        httpx_version = "not installed"

    try:
        import openai as openai_pkg
        openai_version = getattr(openai_pkg, "__version__", "unknown")
    except ImportError:
        openai_version = "not installed"

    proxy_vars = {
        key: os.environ[key]
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy", "ALL_PROXY", "all_proxy")
        if os.environ.get(key)
    }

    print(f"[startup diagnostics] httpx={httpx_version} openai={openai_version}")
    if proxy_vars:
        print(
            f"[startup diagnostics] PROXY ENV VAR(S) PRESENT: {proxy_vars} — "
            "httpx (used by both the openai and anthropic SDKs) respects these "
            "automatically. If a sibling deployment running the same code reaches "
            "OpenAI fine while this one doesn't, compare this line against that "
            "deployment's own startup log first."
        )
    else:
        print("[startup diagnostics] no proxy-related environment variables detected")


_log_startup_diagnostics()

app = FastAPI(
    title="HomeLens API",
    description="Search listings with hard filters, then re-rank with AI based on freeform preferences.",
    version="0.2.0",
)

# Dev-only CORS. Tighten CORS_ALLOW_ORIGINS in .env before deploying anywhere real —
# "*" should never be used in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOW_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(listings.router)
app.include_router(match.router)
app.include_router(vector_search.router)
app.include_router(smart_search.router)


@app.get("/")
def root():
    return {
        "status": "ok",
        "docs": "/docs",
        "data_source": settings.DATA_SOURCE,       # default from .env — overridable per-request
        "ai_provider": settings.AI_PROVIDER,        # default from .env — overridable per-request
        "available_data_sources": list(VALID_DATA_SOURCES),
        "available_ai_providers": list(VALID_AI_PROVIDERS),
    }
