"""
app/models.py

Pydantic request/response models. Separated from routers so the shape of
the API is defined in one place, independent of the handler logic.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional

from app.config import VALID_DATA_SOURCES, VALID_AI_PROVIDERS


class ListingQuery(BaseModel):
    min_price: Optional[int] = Field(None, example=None)
    max_price: Optional[int] = Field(None, example=None)
    min_beds: Optional[int] = Field(None, example=None)
    min_baths: Optional[float] = Field(None, example=None)
    min_sqft: Optional[int] = Field(None, example=None)
    cities: Optional[list[str]] = Field(None, example=["Redwood City"])
    min_school_rating: Optional[int] = Field(
        None, example=None,
        description="1-10. Only has effect on listings with school_ratings data. By default filters by AVERAGE of elementary/middle/high ratings — see strict_school_rating for an all-must-pass alternative."
    )
    strict_school_rating: Optional[bool] = Field(
        None, example=None,
        description="If true, every assigned school (not just the average) must individually meet min_school_rating."
    )
    property_types: Optional[list[str]] = Field(
        None, example=None,
        description='e.g. ["SingleFamilyResidence"] or ["Condominium"].'
    )
    max_hoa: Optional[int] = Field(
        None, example=None,
        description="Monthly HOA fee ceiling in dollars. Listings with no HOA always pass."
    )
    min_stories: Optional[int] = Field(
        None, example=None,
        description="e.g. 2 to require 2+ stories."
    )
    max_stories: Optional[int] = Field(
        None, example=None,
        description="e.g. 1 to require single-story (no stairs)."
    )
    exclude_styles: Optional[list[str]] = Field(
        None, example=None,
        description='e.g. ["Ranch"] — excludes listings with that architectural style.'
    )
    data_source: Optional[str] = Field(
        None, example=None,
        description=f'Overrides DATA_SOURCE from .env for this request only. One of: {VALID_DATA_SOURCES}.'
    )

    @field_validator("data_source")
    @classmethod
    def _validate_data_source(cls, v):
        if v is not None and v not in VALID_DATA_SOURCES:
            raise ValueError(f"data_source must be one of {VALID_DATA_SOURCES}, got '{v}'")
        return v


class MatchRequest(BaseModel):
    filters: ListingQuery
    preferences: str = Field(
        ...,
        example="Quiet street, updated kitchen, a spare room for a home office, not near a busy road."
    )
    ai_provider: Optional[str] = Field(
        None, example=None,
        description=f'Overrides AI_PROVIDER from .env for this request only. One of: {VALID_AI_PROVIDERS}.'
    )

    @field_validator("ai_provider")
    @classmethod
    def _validate_ai_provider(cls, v):
        if v is not None and v not in VALID_AI_PROVIDERS:
            raise ValueError(f"ai_provider must be one of {VALID_AI_PROVIDERS}, got '{v}'")
        return v
