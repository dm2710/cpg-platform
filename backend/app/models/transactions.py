"""
Fact table and Bronze staging layer.

staging_transactions  : raw landing zone — one row per inbound record
fact_transactions     : conformed Silver fact table, star schema FK joins
agg_revenue_daily     : pre-aggregated Gold table (materialized on schedule)
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey,
    Integer, JSON, Numeric, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.mixins import TimestampMixin


# ── staging_transactions (Bronze) ─────────────────────────

class StagingTransaction(Base):
    """
    Raw landing zone for all inbound records regardless of source.
    Nothing in this table is modified after insert — full traceability.
    The pipeline reads unprocessed rows, applies DQ rules, and promotes
    clean rows to fact_transactions.
    """
    __tablename__ = "staging_transactions"

    staging_id:       Mapped[int]              = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_name:      Mapped[str]              = mapped_column(String(80),  nullable=False, index=True)
    raw_payload:      Mapped[dict]             = mapped_column(JSONB,       nullable=False)
    transaction_date: Mapped[Optional[date]]   = mapped_column(Date,   index=True)
    sku_id:           Mapped[Optional[str]]    = mapped_column(String(80))
    category_name:    Mapped[Optional[str]]    = mapped_column(String(120))
    region_name:      Mapped[Optional[str]]    = mapped_column(String(120))
    store_id:         Mapped[Optional[str]]    = mapped_column(String(80))
    revenue:          Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    quantity:         Mapped[Optional[int]]    = mapped_column(Integer)
    currency:         Mapped[str]              = mapped_column(String(3), default="USD", nullable=False)
    unit:             Mapped[Optional[str]]    = mapped_column(String(40))
    processed:        Mapped[bool]             = mapped_column(Boolean, default=False, nullable=False, index=True)
    ingested_at:      Mapped[datetime]         = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at:     Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_message:    Mapped[Optional[str]]    = mapped_column(Text)


# ── dim_source ────────────────────────────────────────────

class DimSource(Base):
    __tablename__ = "dim_source"

    source_id:   Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_name: Mapped[str]           = mapped_column(String(80), nullable=False, unique=True)
    source_type: Mapped[Optional[str]] = mapped_column(String(60))  # pos, ecommerce, crm, manual

    transactions: Mapped[list["FactTransaction"]] = relationship(back_populates="source")


# ── fact_transactions (Silver) ────────────────────────────

class FactTransaction(Base, TimestampMixin):
    """
    Conformed fact table. All FKs resolve to current dimension rows.
    sku_surrogate_id joins to the SCD2 version active on transaction_date.
    revenue is always in USD after normalization.
    """
    __tablename__ = "fact_transactions"

    transaction_id:    Mapped[int]              = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transaction_date:  Mapped[date]             = mapped_column(Date, nullable=False, index=True)
    sku_surrogate_id:  Mapped[Optional[int]]    = mapped_column(BigInteger, ForeignKey("dim_sku.sku_surrogate_id"))
    category_id:       Mapped[Optional[int]]    = mapped_column(Integer,    ForeignKey("dim_product_category.category_id"))
    region_id:         Mapped[Optional[int]]    = mapped_column(Integer,    ForeignKey("dim_region.region_id"))
    store_id:          Mapped[Optional[str]]    = mapped_column(String(80), ForeignKey("dim_store.store_id"))
    source_id:         Mapped[Optional[int]]    = mapped_column(Integer,    ForeignKey("dim_source.source_id"))
    staging_id:        Mapped[Optional[int]]    = mapped_column(BigInteger, ForeignKey("staging_transactions.staging_id"))

    revenue:           Mapped[Decimal]          = mapped_column(Numeric(14, 2), nullable=False)
    revenue_original:  Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    currency_original: Mapped[Optional[str]]    = mapped_column(String(3))
    fx_rate:           Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 6))
    quantity:          Mapped[int]              = mapped_column(Integer, default=1, nullable=False)
    unit_price:        Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))

    # relationships
    sku:      Mapped[Optional["DimSku"]]             = relationship(back_populates="transactions", foreign_keys=[sku_surrogate_id])
    category: Mapped[Optional["DimProductCategory"]] = relationship(back_populates="transactions", foreign_keys=[category_id])
    region:   Mapped[Optional["DimRegion"]]          = relationship(back_populates="transactions",  foreign_keys=[region_id])
    store:    Mapped[Optional["DimStore"]]           = relationship(back_populates="transactions",   foreign_keys=[store_id])
    source:   Mapped[Optional["DimSource"]]          = relationship(back_populates="transactions",   foreign_keys=[source_id])


# ── agg_revenue_daily (Gold) ──────────────────────────────

class AggRevenueDaily(Base):
    """
    Pre-aggregated daily revenue per (category, region).
    Populated by refresh_agg_revenue_daily() after each ingestion run.
    All downstream dashboards and forecasts read from this table.
    """
    __tablename__ = "agg_revenue_daily"

    agg_date:       Mapped[date] = mapped_column(Date,    primary_key=True)
    category_id:    Mapped[int]  = mapped_column(Integer, ForeignKey("dim_product_category.category_id"), primary_key=True)
    region_id:      Mapped[int]  = mapped_column(Integer, ForeignKey("dim_region.region_id"), primary_key=True)
    total_revenue:  Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    total_quantity: Mapped[int]     = mapped_column(Integer, nullable=False)
    txn_count:      Mapped[int]     = mapped_column(Integer, nullable=False)
    refreshed_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# imports needed for relationships defined in dimensions.py
from app.models.dimensions import (  # noqa: E402
    DimProductCategory,
    DimSku,
    DimRegion,
    DimStore,
)
