"""
Pydantic schemas for reference data — SKU catalog, stores, regions,
demographics, signals (campaigns, promos, competitor pricing).
"""

from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import Field, field_validator

from app.schemas.base import CamelBase


# ── SKU / Product catalog ─────────────────────────────────

class SkuUpsertRequest(CamelBase):
    sku_id:           str            = Field(..., max_length=80)
    sku_name:         str            = Field(..., max_length=255)
    category_name:    str            = Field(..., max_length=120)
    brand:            Optional[str]  = Field(None, max_length=120)
    sub_category:     Optional[str]  = Field(None, max_length=120)
    package_size:     Optional[str]  = Field(None, max_length=80)
    list_price:       Optional[Decimal] = None
    cost_price:       Optional[Decimal] = None
    launch_date:      Optional[date] = None
    discontinue_date: Optional[date] = None
    is_active:        bool           = True
    effective_date:   Optional[date] = None
    change_reason:    Optional[str]  = None

    @field_validator("list_price", "cost_price")
    @classmethod
    def price_non_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("Price must be non-negative")
        return v


class SkuOut(CamelBase):
    sku_surrogate_id: int
    sku_id:           str
    sku_name:         str
    brand:            Optional[str]
    category_name:    Optional[str]
    sub_category:     Optional[str]
    package_size:     Optional[str]
    list_price:       Optional[Decimal]
    launch_date:      Optional[date]
    is_active:        bool
    is_current:       bool
    valid_from:       date
    valid_to:         date
    change_reason:    Optional[str]


class SkuHistoryOut(CamelBase):
    versions: list[SkuOut]
    sku_id:   str
    total:    int


class BulkSkuRequest(CamelBase):
    records:        list[SkuUpsertRequest]
    effective_date: Optional[date] = None
    change_reason:  str            = "bulk_load"


class BulkSkuResponse(CamelBase):
    inserted:      int
    updated:       int
    unchanged:     int
    errors:        int
    error_details: list[dict] = Field(default_factory=list)


# ── Store ─────────────────────────────────────────────────

class StoreUpsertRequest(CamelBase):
    store_id:    str            = Field(..., max_length=80)
    store_name:  str            = Field(..., max_length=255)
    region_name: str            = Field(..., max_length=120)
    store_type:  Optional[str]  = Field(None, max_length=60)
    country:     Optional[str]  = Field(None, max_length=80)
    city:        Optional[str]  = Field(None, max_length=120)
    latitude:    Optional[float] = None
    longitude:   Optional[float] = None
    timezone:    Optional[str]  = Field(None, max_length=60)
    opened_date: Optional[date] = None
    closed_date: Optional[date] = None
    is_active:   bool           = True
    sq_footage:  Optional[int]  = None

    @field_validator("latitude")
    @classmethod
    def validate_lat(cls, v):
        if v is not None and not (-90 <= v <= 90):
            raise ValueError("Latitude must be between -90 and 90")
        return v

    @field_validator("longitude")
    @classmethod
    def validate_lon(cls, v):
        if v is not None and not (-180 <= v <= 180):
            raise ValueError("Longitude must be between -180 and 180")
        return v


class StoreOut(CamelBase):
    store_id:    str
    store_name:  str
    region_name: Optional[str]
    store_type:  Optional[str]
    country:     Optional[str]
    city:        Optional[str]
    latitude:    Optional[Decimal]
    longitude:   Optional[Decimal]
    is_active:   bool
    opened_date: Optional[date]


# ── Region demographics ───────────────────────────────────

class DemographicsUpsertRequest(CamelBase):
    region_name:              str            = Field(..., max_length=120)
    snapshot_year:            int            = Field(..., ge=2000, le=2100)
    population:               Optional[int]  = None
    median_income_usd:        Optional[Decimal] = None
    urban_pct:                Optional[Decimal] = Field(None, ge=0, le=100)
    age_median:               Optional[Decimal] = None
    gdp_per_capita_usd:       Optional[Decimal] = None
    internet_penetration_pct: Optional[Decimal] = Field(None, ge=0, le=100)


# ── Marketing campaigns ───────────────────────────────────

class CampaignUpsertRequest(CamelBase):
    campaign_id:          str            = Field(..., max_length=80)
    campaign_name:        str            = Field(..., max_length=255)
    start_date:           date
    end_date:             date
    channel:              Optional[str]  = Field(None, max_length=60)
    campaign_type:        Optional[str]  = Field(None, max_length=60)
    budget_usd:           Optional[Decimal] = None
    target_category_name: Optional[str]  = None
    target_region_name:   Optional[str]  = None
    target_sku_id:        Optional[str]  = None
    impressions:          Optional[int]  = None
    clicks:               Optional[int]  = None
    conversions:          Optional[int]  = None

    @field_validator("end_date")
    @classmethod
    def end_after_start(cls, v, info):
        if "start_date" in info.data and v < info.data["start_date"]:
            raise ValueError("end_date must be on or after start_date")
        return v


class CampaignOut(CamelBase):
    campaign_id:   str
    campaign_name: str
    channel:       Optional[str]
    campaign_type: Optional[str]
    start_date:    date
    end_date:      date
    budget_usd:    Optional[Decimal]
    impressions:   Optional[int]
    clicks:        Optional[int]
    conversions:   Optional[int]


# ── Promo windows ─────────────────────────────────────────

class PromoUpsertRequest(CamelBase):
    promo_id:        str            = Field(..., max_length=80)
    promo_name:      str            = Field(..., max_length=255)
    start_date:      date
    end_date:        date
    promo_type:      Optional[str]  = Field(None, max_length=60)
    discount_pct:    Optional[Decimal] = Field(None, ge=0, le=100)
    sku_id:          Optional[str]  = None
    category_name:   Optional[str]  = None
    region_name:     Optional[str]  = None
    min_order_value: Optional[Decimal] = None
    channel:         Optional[str]  = Field(None, max_length=60)


class PromoOut(CamelBase):
    promo_id:     str
    promo_name:   str
    promo_type:   Optional[str]
    discount_pct: Optional[Decimal]
    start_date:   date
    end_date:     date
    sku_id:       Optional[str]
    channel:      Optional[str]


# ── Competitor pricing ────────────────────────────────────

class CompetitorPriceRequest(CamelBase):
    snapshot_date:         date
    competitor_name:       str     = Field(..., max_length=120)
    our_price_usd:         Decimal = Field(..., gt=0)
    competitor_price_usd:  Decimal = Field(..., gt=0)
    sku_id:                Optional[str]  = None
    category_name:         Optional[str]  = None
    region_name:           Optional[str]  = None
    data_source:           str            = "manual"


class CompetitorPriceOut(CamelBase):
    id:                   int
    snapshot_date:        date
    competitor_name:      str
    our_price_usd:        Optional[Decimal]
    competitor_price_usd: Optional[Decimal]
    price_index:          Optional[Decimal]
    data_source:          Optional[str]
