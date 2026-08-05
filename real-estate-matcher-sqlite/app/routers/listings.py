"""
app/routers/listings.py

/listings — hard-filter search only, no AI involved. This is what "Browse
all (skip AI)" in the frontend calls; it never imports or touches
matching_service, so it genuinely never calls Claude/OpenAI.
"""

from fastapi import APIRouter, HTTPException

from app.models import ListingQuery
from app.services.listings_service import (
    build_hard_filters, fetch_listings, normalize_listing, filter_by_school_rating,
)

router = APIRouter()


@router.post("/listings")
def get_listings(query: ListingQuery):
    """Hard-filter search only. Returns raw structured listings, no scoring."""
    filters = build_hard_filters(query)
    try:
        raw = fetch_listings(filters, data_source=query.data_source)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Listings source request failed: {e}")

    listings = [normalize_listing(r) for r in raw]
    listings = filter_by_school_rating(listings, query.min_school_rating, query.strict_school_rating or False)
    return {"count": len(listings), "listings": listings}
