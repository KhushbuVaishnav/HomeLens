"""
app/routers/smart_search.py

/smart-search/classify — the "plan" step for the 5th, agent-routed search
mode. Decomposes the buyer's freeform text into requirement clauses
(matching_service.classify_query), then deterministically decides which of
the existing 3 execution paths should handle the search
(router_service.decide_route).

Deliberately does NOT run a search itself — no import of vector_service,
and match.py's job machinery isn't touched here either. This endpoint only
decides; the frontend calls whichever endpoint the decision names, exactly
like it already does for the other 4 tabs. That keeps this endpoint's cost
to always just the one small classify call, never a full search.
"""

from fastapi import APIRouter, HTTPException

from app.models import MatchRequest
from app.services import matching_service, router_service

router = APIRouter()


@router.post("/smart-search/classify")
def classify(request: MatchRequest):
    """Returns the routing decision — route, human-readable reason, and the
    underlying requirement decomposition — without running any search."""
    try:
        requirements = matching_service.classify_query(request.preferences, request.ai_provider)
    except matching_service.MatchingError as e:
        print(f"[smart-search error] {e.technical_detail}")
        raise HTTPException(status_code=502, detail=e.client_message)
    except Exception as e:
        print(f"[smart-search error] unexpected exception type {type(e).__name__}: {e}")
        raise HTTPException(status_code=502, detail=matching_service._CLIENT_MSG_UNKNOWN)

    route, reason = router_service.decide_route(request.preferences, requirements)
    print(f"[smart-search] route={route} requirements={len(requirements)} reason={reason}")
    return {"route": route, "reason": reason, "requirements": requirements}
