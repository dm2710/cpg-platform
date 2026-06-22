"""
Secondary signal models — demand explainers used as forecast covariates.

marketing_campaigns  : campaign metadata + spend + reach by date window
promo_windows        : promotional discounts by SKU / category / region
competitor_pricing   : periodic competitive price snapshots
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey,
    Integer, Numeric, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.mixins import TimestampMixin


class MarketingCampaign(Base, TimestampMixin):
    __tablename__ = "marketing_campaigns"

    campaign_id:        Mapped[str]              = mapped_column(String(80), primary_key=True)
    campaign_name:      Mapped[str]              = mapped_column(String(255), nullable=False)
    channel:            Mapped[Optional[str]]    = mapped_column(String(60))  # email, social, tv, paid_search
    campaign_type:      Mapped[Optional[str]]    = mapped_column(String(60))  # awareness, conversion, retention
    start_date:         Mapped[date]             = mapped_column(Date, nullable=False, index=True)
    end_date:           Mapped[date]             = mapped_column(Date, nullable=False, index=True)
    budget_usd:         Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    target_category_id: Mapped[Optional[int]]    = mapped_column(Integer, ForeignKey("dim_product_category.category_id"))
    target_region_id:   Mapped[Optional[int]]    = mapped_column(Integer, ForeignKey("dim_region.region_id"))
    target_sku_id:      Mapped[Optional[str]]    = mapped_column(String(80))
    impressions:        Mapped[Optional[int]]    = mapped_column(BigInteger)
    clicks:             Mapped[Optional[int]]    = mapped_column(BigInteger)
    conversions:        Mapped[Optional[int]]    = mapped_column(Integer)


class PromoWindow(Base, TimestampMixin):
    __tablename__ = "promo_windows"

    promo_id:        Mapped[str]              = mapped_column(String(80), primary_key=True)
    promo_name:      Mapped[str]              = mapped_column(String(255), nullable=False)
    promo_type:      Mapped[Optional[str]]    = mapped_column(String(60))  # pct_off, bogo, bundle, free_shipping
    discount_pct:    Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    start_date:      Mapped[date]             = mapped_column(Date, nullable=False, index=True)
    end_date:        Mapped[date]             = mapped_column(Date, nullable=False, index=True)
    sku_id:          Mapped[Optional[str]]    = mapped_column(String(80))
    category_id:     Mapped[Optional[int]]    = mapped_column(Integer, ForeignKey("dim_product_category.category_id"))
    region_id:       Mapped[Optional[int]]    = mapped_column(Integer, ForeignKey("dim_region.region_id"))
    min_order_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    channel:         Mapped[Optional[str]]    = mapped_column(String(60))  # online, in-store, all


class CompetitorPricing(Base, TimestampMixin):
    __tablename__ = "competitor_pricing"

    id:                    Mapped[int]              = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_date:         Mapped[date]             = mapped_column(Date, nullable=False, index=True)
    competitor_name:       Mapped[str]              = mapped_column(String(120), nullable=False)
    sku_id:                Mapped[Optional[str]]    = mapped_column(String(80))
    category_id:           Mapped[Optional[int]]    = mapped_column(Integer, ForeignKey("dim_product_category.category_id"))
    our_price_usd:         Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    competitor_price_usd:  Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    price_index:           Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 3))  # competitor / ours
    region_id:             Mapped[Optional[int]]    = mapped_column(Integer, ForeignKey("dim_region.region_id"))
    data_source:           Mapped[Optional[str]]    = mapped_column(String(80))
