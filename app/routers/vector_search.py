"""
app/routers/vector_search.py

/vector-search — standalone, experimental 4th search mode. Hard filters,
then ranks by pure embedding cosine similarity against listing
descriptions. No LLM call anywhere in this path — no matching_service
import, same "structurally cannot reach the AI" guarantee /listings
already gives Traditional mode, just for a different reason (this mode's
whole point is comparing AGAINST the AI-scored modes, so it must never
accidentally call one).

Synchronous, single request/response — no job_id, no polling. The
/match/* job+polling machinery exists specifically to keep the frontend
responsive through slow, multi-batch LLM calls; a brute-force similarity
scan over hundreds of listings is sub-millisecond work (verified directly
against this same listings data), so there's nothing here that needs it.
"""

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.models import MatchRequest
from app.services.listings_service import build_hard_filters, fetch_listings, normalize_listing
from app.services import vector_service

router = APIRouter()


@router.post("/vector-search")
def vector_search(request: MatchRequest):
    """Hard filters, then semantic similarity ranking — no AI scoring."""
    filters = build_hard_filters(request.filters)
    try:
        raw = fetch_listings(filters, data_source=request.filters.data_source)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Listings source request failed: {e}")

    listings = [normalize_listing(r) for r in raw]
    data_source = request.filters.data_source or settings.DATA_SOURCE

    try:
        results = vector_service.semantic_search(request.preferences, listings, data_source)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # top_k/candidate_pool let the frontend say what actually happened —
    # "top 20 of 500 candidates by similarity," not just a bare count that
    # reads like "only 20 listings matched." See VECTOR_TOP_K in config.py.
    return {
        "count": len(results),
        "matches": results,
        "top_k": settings.VECTOR_TOP_K,
        "candidate_pool": len(listings),
    }
