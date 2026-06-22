"""
Dimension tables — the reference data layer.

dim_sku          : Product / SKU master, SCD Type 2
dim_store        : Store master with lat/lon and store type
dim_region       : Region master with country / sub-region
dim_region_demo  : Annual demographic snapshots per region
dim_calendar     : Pre-populated date spine with retail annotations
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey,
    Integer, Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.mixins import TimestampMixin


# ── dim_product_category ──────────────────────────────────

class DimProductCategory(Base):
    __tablename__ = "dim_product_category"

    category_id:    Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_name:  Mapped[str]           = mapped_column(String(120), nullable=False, unique=True)
    category_group: Mapped[Optional[str]] = mapped_column(String(120))

    # relationships
    skus:         Mapped[list["DimSku"]]     = relationship(back_populates="category")
    transactions: Mapped[list["FactTransaction"]] = relationship(back_populates="category")


# ── dim_sku (SCD Type 2) ──────────────────────────────────

class DimSku(Base, TimestampMixin):
    __tablename__ = "dim_sku"
    __table_args__ = (
        UniqueConstraint("sku_id", name="uq_sku_current_active"),  # enforced in code for SCD2
    )

    sku_surrogate_id:    Mapped[int]              = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sku_id:              Mapped[str]              = mapped_column(String(80),  nullable=False, index=True)
    sku_name:            Mapped[str]              = mapped_column(String(255), nullable=False)
    brand:               Mapped[Optional[str]]    = mapped_column(String(120))
    category_id:         Mapped[Optional[int]]    = mapped_column(Integer, ForeignKey("dim_product_category.category_id"))
    sub_category:        Mapped[Optional[str]]    = mapped_column(String(120))
    package_size:        Mapped[Optional[str]]    = mapped_column(String(80))
    package_size_units:  Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 3))
    package_unit:        Mapped[Optional[str]]    = mapped_column(String(40))
    list_price:          Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    cost_price:          Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    launch_date:         Mapped[Optional[date]]   = mapped_column(Date)
    discontinue_date:    Mapped[Optional[date]]   = mapped_column(Date)
    is_active:           Mapped[bool]             = mapped_column(Boolean, default=True, nullable=False)

    # SCD2 validity window
    valid_from:     Mapped[date] = mapped_column(Date, nullable=False)
    valid_to:       Mapped[date] = mapped_column(Date, nullable=False, default=date(9999, 12, 31))
    is_current:     Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    change_reason:  Mapped[Optional[str]] = mapped_column(Text)

    # relationships
    category:     Mapped[Optional["DimProductCategory"]] = relationship(back_populates="skus")
    transactions: Mapped[list["FactTransaction"]]        = relationship(back_populates="sku")


# ── dim_region ────────────────────────────────────────────

class DimRegion(Base):
    __tablename__ = "dim_region"

    region_id:   Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    region_name: Mapped[str]           = mapped_column(String(120), nullable=False, unique=True)
    country:     Mapped[Optional[str]] = mapped_column(String(80))
    sub_region:  Mapped[Optional[str]] = mapped_column(String(120))

    # relationships
    stores:         Mapped[list["DimStore"]]              = relationship(back_populates="region")
    demographics:   Mapped[list["DimRegionDemographics"]] = relationship(back_populates="region")
    weather:        Mapped[list["WeatherDaily"]]          = relationship(back_populates="region")
    transactions:   Mapped[list["FactTransaction"]]       = relationship(back_populates="region")


# ── dim_store ─────────────────────────────────────────────

class DimStore(Base, TimestampMixin):
    __tablename__ = "dim_store"

    store_id:     Mapped[str]              = mapped_column(String(80),  primary_key=True)
    store_name:   Mapped[str]              = mapped_column(String(255), nullable=False)
    store_type:   Mapped[Optional[str]]    = mapped_column(String(60))   # flagship, outlet, online, kiosk
    region_id:    Mapped[Optional[int]]    = mapped_column(Integer, ForeignKey("dim_region.region_id"))
    country:      Mapped[Optional[str]]    = mapped_column(String(80))
    city:         Mapped[Optional[str]]    = mapped_column(String(120))
    latitude:     Mapped[Optional[Decimal]] = mapped_column(Numeric(9, 6))
    longitude:    Mapped[Optional[Decimal]] = mapped_column(Numeric(9, 6))
    timezone:     Mapped[Optional[str]]    = mapped_column(String(60))
    opened_date:  Mapped[Optional[date]]   = mapped_column(Date)
    closed_date:  Mapped[Optional[date]]   = mapped_column(Date)
    is_active:    Mapped[bool]             = mapped_column(Boolean, default=True, nullable=False)
    sq_footage:   Mapped[Optional[int]]    = mapped_column(Integer)

    # relationships
    region:       Mapped[Optional["DimRegion"]]   = relationship(back_populates="stores")
    transactions: Mapped[list["FactTransaction"]] = relationship(back_populates="store")


# ── dim_region_demographics ───────────────────────────────

class DimRegionDemographics(Base):
    __tablename__ = "dim_region_demographics"
    __table_args__ = (
        UniqueConstraint("region_id", "snapshot_year", name="uq_region_demo_year"),
    )

    id:                       Mapped[int]              = mapped_column(Integer, primary_key=True, autoincrement=True)
    region_id:                Mapped[int]              = mapped_column(Integer, ForeignKey("dim_region.region_id"), nullable=False)
    snapshot_year:            Mapped[int]              = mapped_column(Integer, nullable=False)
    population:               Mapped[Optional[int]]    = mapped_column(BigInteger)
    median_income_usd:        Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    urban_pct:                Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    age_median:               Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 1))
    gdp_per_capita_usd:       Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    internet_penetration_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))

    region: Mapped["DimRegion"] = relationship(back_populates="demographics")


# ── dim_calendar ──────────────────────────────────────────

class DimCalendar(Base):
    __tablename__ = "dim_calendar"

    cal_date:          Mapped[date]           = mapped_column(Date, primary_key=True)
    year:              Mapped[int]            = mapped_column(Integer, nullable=False)
    quarter:           Mapped[int]            = mapped_column(Integer, nullable=False)
    month:             Mapped[int]            = mapped_column(Integer, nullable=False)
    week_of_year:      Mapped[int]            = mapped_column(Integer, nullable=False)
    day_of_week:       Mapped[int]            = mapped_column(Integer, nullable=False)  # 0=Mon
    is_weekend:        Mapped[bool]           = mapped_column(Boolean, nullable=False)
    is_public_holiday: Mapped[bool]           = mapped_column(Boolean, default=False, nullable=False)
    holiday_name:      Mapped[Optional[str]]  = mapped_column(String(120))
    retail_season:     Mapped[Optional[str]]  = mapped_column(String(60))
    fiscal_week:       Mapped[Optional[int]]  = mapped_column(Integer)
    fiscal_quarter:    Mapped[Optional[int]]  = mapped_column(Integer)
    fiscal_year:       Mapped[Optional[int]]  = mapped_column(Integer)


# ── weather_daily ─────────────────────────────────────────

class WeatherDaily(Base):
    __tablename__ = "weather_daily"
    __table_args__ = (
        UniqueConstraint("weather_date", "region_id", name="uq_weather_date_region"),
    )

    id:                 Mapped[int]              = mapped_column(Integer, primary_key=True, autoincrement=True)
    weather_date:       Mapped[date]             = mapped_column(Date, nullable=False, index=True)
    region_id:          Mapped[int]              = mapped_column(Integer, ForeignKey("dim_region.region_id"), nullable=False)
    avg_temp_c:         Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    max_temp_c:         Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    min_temp_c:         Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    precipitation_mm:   Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 2))
    snowfall_mm:        Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 2))
    is_extreme_weather: Mapped[bool]             = mapped_column(Boolean, default=False)
    weather_source:     Mapped[Optional[str]]    = mapped_column(String(80))

    region: Mapped["DimRegion"] = relationship(back_populates="weather")
