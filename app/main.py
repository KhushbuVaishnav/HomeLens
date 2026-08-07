"""
app/main.py

FastAPI app assembly. Run with:
    uvicorn app.main:app --reload
from the project root (not from inside app/).

Then open http://127.0.0.1:8000/docs for interactive Swagger docs.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings, VALID_DATA_SOURCES, VALID_AI_PROVIDERS
from app.routers import listings, match

settings.validate()  # fail fast on misconfiguration (bad DATA_SOURCE, missing API key, etc.)

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
