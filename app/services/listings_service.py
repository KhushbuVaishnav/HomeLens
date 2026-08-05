"""
app/services/listings_service.py

Fetches and filters listings from whichever data source app.config.settings
points at (SimplyRETS live API, or one of the local JSON datasets). Filter
logic is identical regardless of source, applied client-side after loading.
"""

import json
import requests
from dataclasses import dataclass
from typing import Optional

from app.config import settings, VALID_DATA_SOURCES


@dataclass
class HardFilters:
    min_price: Optional[int] = None
    max_price: Optional[int] = None
    min_beds: Optional[int] = None
    max_beds: Optional[int] = None
    min_baths: Optional[float] = None
    min_sqft: Optional[int] = None
    cities: Optional[list] = None
    postal_codes: Optional[list] = None
    property_types: Optional[list] = None      # e.g. ["SingleFamilyResidence"], ["Condominium"]
    max_hoa: Optional[int] = None               # monthly HOA fee ceiling; no-HOA listings always pass
    min_stories: Optional[int] = None            # e.g. 2 to require 2+ stories
    max_stories: Optional[int] = None            # e.g. 1 to require exactly single-story
    exclude_styles: Optional[list] = None        # e.g. ["Ranch"]


def build_hard_filters(query) -> HardFilters:
    """Builds a HardFilters from a ListingQuery (or anything shaped like one —
    MatchRequest.filters IS a ListingQuery). Shared by both routers instead
    of each duplicating this field-by-field mapping separately."""
    return HardFilters(
        min_price=query.min_price,
        max_price=query.max_price,
        min_beds=query.min_beds,
        min_baths=query.min_baths,
        min_sqft=query.min_sqft,
        cities=query.cities,
        property_types=query.property_types,
        max_hoa=query.max_hoa,
        min_stories=query.min_stories,
        max_stories=query.max_stories,
        exclude_styles=query.exclude_styles,
    )

def fetch_listings(filters: HardFilters, limit: int = None, data_source: str = None) -> list[dict]:
    """
    Returns raw listing dicts from the given data_source, or
    settings.DATA_SOURCE (the .env default) if none is passed. Applies
    price/beds/baths/city/sqft/property-type/HOA/stories/style filters
    uniformly regardless of source.
    """
    limit = limit or settings.DEFAULT_FETCH_LIMIT
    source = data_source or settings.DATA_SOURCE
    if source not in VALID_DATA_SOURCES:
        raise ValueError(f"data_source must be one of {VALID_DATA_SOURCES}, got '{source}'")

    if source == "generated":
        listings = _fetch_from_json(settings.GENERATED_DATA_PATH, filters, limit)
    elif source == "realistic":
        listings = _fetch_from_json(settings.REALISTIC_DATA_PATH, filters, limit)
    elif source == "sample":
        listings = _fetch_from_json(settings.SAMPLE_DATA_PATH, filters, limit)
    else:
        listings = _fetch_from_simplyrets(filters, limit)

    if filters.min_sqft:
        listings = [l for l in listings if (l.get("property", {}).get("area") or 0) >= filters.min_sqft]

    if filters.property_types:
        listings = [l for l in listings if l.get("property", {}).get("subType") in filters.property_types]

    if filters.max_hoa is not None:
        listings = [l for l in listings if (l.get("association", {}).get("fee") or 0) <= filters.max_hoa]

    if filters.min_stories is not None:
        listings = [l for l in listings if (l.get("property", {}).get("stories") or 0) >= filters.min_stories]

    if filters.max_stories is not None:
        listings = [l for l in listings if (l.get("property", {}).get("stories") or 0) <= filters.max_stories]


    if filters.exclude_styles:
        listings = [l for l in listings if l.get("property", {}).get("style") not in filters.exclude_styles]

    return listings


def _fetch_from_simplyrets(filters: HardFilters, limit: int) -> list[dict]:
    params = {"type": "residential", "limit": limit}
    if filters.min_price:
        params["minprice"] = filters.min_price
    if filters.max_price:
        params["maxprice"] = filters.max_price
    if filters.min_beds:
        params["minbeds"] = filters.min_beds
    if filters.max_beds:
        params["maxbeds"] = filters.max_beds
    if filters.min_baths:
        params["minbaths"] = filters.min_baths
    if filters.cities:
        params["cities"] = ",".join(filters.cities)
    if filters.postal_codes:
        params["postalCodes"] = ",".join(filters.postal_codes)

    resp = requests.get(settings.SIMPLYRETS_BASE_URL, params=params, auth=settings.SIMPLYRETS_AUTH, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _matches_basic_filters(l: dict, filters: HardFilters) -> bool:
    prop = l.get("property", {})
    addr = l.get("address", {})
    price = l.get("listPrice", 0)

    if filters.min_price and price < filters.min_price:
        return False
    if filters.max_price and price > filters.max_price:
        return False
    if filters.min_beds and (prop.get("bedrooms") or 0) < filters.min_beds:
        return False
    if filters.max_beds and (prop.get("bedrooms") or 0) > filters.max_beds:
        return False
    if filters.min_baths and (prop.get("bathsFull") or 0) < filters.min_baths:
        return False
    if filters.cities and addr.get("city") not in filters.cities:
        return False
    if filters.postal_codes and addr.get("postalCode") not in filters.postal_codes:
        return False
    return True


def _fetch_from_json(path, filters: HardFilters, limit: int) -> list[dict]:
    if not path.exists():
        hint = " Run `python scripts/generate_listings.py` first." if "generated" in path.name else ""
        raise FileNotFoundError(f"Data file not found: {path}.{hint}")
    with open(path) as f:
        listings = json.load(f)
    return [l for l in listings if _matches_basic_filters(l, filters)][:limit]


def normalize_listing(raw: dict) -> dict:
    """
    Flattens SimplyRETS-shaped nested JSON into the flat dict the rest of
    the app expects. Handles the sandbox's identical-boilerplate remarks
    problem by building a description from differentiated fields instead
    when boilerplate is detected.
    """
    prop = raw.get("property", {})
    addr = raw.get("address", {})

    real_remarks = raw.get("remarks", "")
    is_boilerplate = "trial property to test the SimplyRETS" in real_remarks

    if is_boilerplate:
        parts = [
            prop.get("style", ""),
            f"Additional rooms: {prop.get('additionalRooms')}" if prop.get("additionalRooms") else "",
            f"Interior: {prop.get('interiorFeatures')}" if prop.get("interiorFeatures") else "",
            f"Exterior: {prop.get('exteriorFeatures')}" if prop.get("exteriorFeatures") else "",
            f"Lot: {prop.get('lotDescription')}" if prop.get("lotDescription") else "",
            f"View: {prop.get('view')}" if prop.get("view") else "",
        ]
        description = ". ".join(p for p in parts if p)
    else:
        description = real_remarks

    listing = {
        "mls_id": raw.get("mlsId"),
        "price": raw.get("listPrice"),
        "address": addr.get("full"),
        "city": addr.get("city"),
        "state": addr.get("state"),
        "beds": prop.get("bedrooms"),
        "baths": prop.get("bathsFull"),
        "sqft": prop.get("area"),
        "year_built": prop.get("yearBuilt"),
        "lot_size": prop.get("lotSize"),
        "description": description,
        "photos": raw.get("photos", []),
        "property_type": prop.get("subType"),
        "style": prop.get("style"),
        "stories": prop.get("stories"),
        "hoa_fee": raw.get("association", {}).get("fee"),
    }

    if raw.get("schools"):
        from app.services.schools_service import attach_school_ratings
        listing = attach_school_ratings(listing, raw["schools"])

    return listing


def filter_by_school_rating(listings: list[dict], min_rating: Optional[int], strict: bool = False) -> list[dict]:
    """Filters by a listing's school ratings. No-op if listing has none.

    strict=False (default): passes if the AVERAGE of elementary/middle/high
        ratings meets min_rating — e.g. 9/7/8 averages to 8.0, passes for
        min_rating=8 even though the middle school alone is only a 7.
    strict=True: passes only if EVERY assigned school individually meets
        min_rating — the 9/7/8 example above would fail, since 7 < 8.
    """
    if not min_rating:
        return listings

    def passes(l: dict) -> bool:
        ratings = l.get("school_ratings")
        if not ratings:
            return True
        values = [r["rating"] for r in ratings.values() if r.get("rating") is not None]
        if not values:
            return True
        if strict:
            return all(v >= min_rating for v in values)
        return (sum(values) / len(values)) >= min_rating

    return [l for l in listings if passes(l)]
