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
from app.routers import listings, match

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

# ============================================================
# TEMPORARY OPENAI / NETWORK DIAGNOSTICS
# Remove after debugging.
# ============================================================

import os
import sys
import socket
import ssl
import traceback

import httpx
import openai
from openai import OpenAI


def print_exception_chain(exc):
    """
    Print nested exception causes without hiding the useful
    httpx / SSL / DNS exception underneath OpenAI's error.
    """
    print(f"[diag] exception type: {type(exc).__name__}")
    print(f"[diag] exception repr: {repr(exc)}")

    current = exc.__cause__
    level = 1

    while current is not None:
        print(
            f"[diag] cause {level}: "
            f"{type(current).__name__}: {repr(current)}"
        )
        current = current.__cause__
        level += 1


def run_openai_diagnostics():
    print("")
    print("========================================")
    print(" OPENAI CONNECTIVITY DIAGNOSTICS")
    print("========================================")

    # --------------------------------------------------------
    # 1. Runtime versions
    # --------------------------------------------------------

    print(f"[diag] python={sys.version}")
    print(f"[diag] openai={openai.__version__}")
    print(f"[diag] httpx={httpx.__version__}")
    print(f"[diag] openssl={ssl.OPENSSL_VERSION}")

    # --------------------------------------------------------
    # 2. Relevant environment variables
    # DO NOT print the actual API key.
    # --------------------------------------------------------

    api_key = os.getenv("OPENAI_API_KEY")

    print(
        "[diag] OPENAI_API_KEY present=",
        bool(api_key)
    )

    print(
        "[diag] OPENAI_API_KEY length=",
        len(api_key) if api_key else 0
    )

    print(
        "[diag] OPENAI_BASE_URL=",
        repr(os.getenv("OPENAI_BASE_URL"))
    )

    print(
        "[diag] HTTP_PROXY=",
        repr(os.getenv("HTTP_PROXY"))
    )

    print(
        "[diag] HTTPS_PROXY=",
        repr(os.getenv("HTTPS_PROXY"))
    )

    print(
        "[diag] ALL_PROXY=",
        repr(os.getenv("ALL_PROXY"))
    )

    print(
        "[diag] NO_PROXY=",
        repr(os.getenv("NO_PROXY"))
    )

    print(
        "[diag] SSL_CERT_FILE=",
        repr(os.getenv("SSL_CERT_FILE"))
    )

    print(
        "[diag] SSL_CERT_DIR=",
        repr(os.getenv("SSL_CERT_DIR"))
    )

    # --------------------------------------------------------
    # 3. DNS test
    # Can this Vertex/Cloud Run container resolve api.openai.com?
    # --------------------------------------------------------

    print("")
    print("----- DNS TEST -----")

    try:
        results = socket.getaddrinfo(
            "api.openai.com",
            443,
            type=socket.SOCK_STREAM,
        )

        addresses = sorted(
            {
                result[4][0]
                for result in results
            }
        )

        print("[diag] DNS SUCCESS")
        print("[diag] api.openai.com addresses:", addresses)

    except Exception as exc:
        print("[diag] DNS FAILED")
        print_exception_chain(exc)
        traceback.print_exc()

    # --------------------------------------------------------
    # 4. RAW HTTPS TEST
    #
    # No API key intentionally.
    #
    # SUCCESS here will usually mean we receive an HTTP response
    # such as 401 from OpenAI.
    #
    # 401 = GOOD for this particular diagnostic.
    # It proves DNS/TCP/TLS/HTTPS worked.
    # --------------------------------------------------------

    print("")
    print("----- RAW HTTPS TEST -----")

    try:
        with httpx.Client(
            timeout=15.0,
            follow_redirects=True,
        ) as client:

            response = client.get(
                "https://api.openai.com/v1/models"
            )

            print("[diag] RAW HTTPS SUCCESS")
            print("[diag] status_code=", response.status_code)
            print(
                "[diag] server=",
                response.headers.get("server")
            )

    except Exception as exc:
        print("[diag] RAW HTTPS FAILED")
        print_exception_chain(exc)
        traceback.print_exc()

    # --------------------------------------------------------
    # 5. RAW HTTPS TEST WITHOUT ENVIRONMENT/PROXY SETTINGS
    #
    # This helps determine whether environment-derived HTTP
    # configuration is interfering.
    # --------------------------------------------------------

    print("")
    print("----- RAW HTTPS TEST trust_env=False -----")

    try:
        with httpx.Client(
            timeout=15.0,
            follow_redirects=True,
            trust_env=False,
        ) as client:

            response = client.get(
                "https://api.openai.com/v1/models"
            )

            print("[diag] RAW HTTPS trust_env=False SUCCESS")
            print("[diag] status_code=", response.status_code)

    except Exception as exc:
        print("[diag] RAW HTTPS trust_env=False FAILED")
        print_exception_chain(exc)
        traceback.print_exc()

    # --------------------------------------------------------
    # 6. OPENAI SDK TEST
    #
    # Explicitly hard-code OpenAI's normal base URL so that
    # OPENAI_BASE_URL cannot accidentally redirect this test.
    #
    # max_retries=0 is deliberate so we see the original error
    # immediately instead of waiting through retries.
    # --------------------------------------------------------

    print("")
    print("----- OPENAI SDK TEST -----")

    if not api_key:
        print(
            "[diag] SDK TEST SKIPPED: "
            "OPENAI_API_KEY is not available"
        )

    else:
        try:
            test_client = OpenAI(
                api_key=api_key,
                base_url="https://api.openai.com/v1",
                timeout=15.0,
                max_retries=0,
            )

            print(
                "[diag] SDK client base_url=",
                str(test_client.base_url)
            )

            result = test_client.models.list()

            print("[diag] OPENAI SDK SUCCESS")
            print(
                "[diag] model list returned count=",
                len(result.data)
            )

        except Exception as exc:
            print("[diag] OPENAI SDK FAILED")
            print_exception_chain(exc)
            traceback.print_exc()

    print("")
    print("========================================")
    print(" END OPENAI CONNECTIVITY DIAGNOSTICS")
    print("========================================")
    print("")


# Run once when the container starts.
run_openai_diagnostics()

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
