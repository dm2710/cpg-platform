"""
Phase 2 forecasting models.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey,
    Integer, Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.mixins import TimestampMixin


class ModelRegistry(Base):
    __tablename__ = "model_registry"
    __table_args__ = (
        UniqueConstraint("model_name", "segment_key", "model_version", name="uq_model_version"),
    )

    model_id:          Mapped[int]             = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    model_name:        Mapped[str]             = mapped_column(String(80),  nullable=False)
    model_version:     Mapped[str]             = mapped_column(String(40),  nullable=False)
    category_id:       Mapped[Optional[int]]   = mapped_column(Integer, ForeignKey("dim_product_category.category_id"))
    region_id:         Mapped[Optional[int]]   = mapped_column(Integer, ForeignKey("dim_region.region_id"))
    segment_key:       Mapped[str]             = mapped_column(String(120), nullable=False, index=True)
    status:            Mapped[str]             = mapped_column(String(20),  nullable=False, default="trained")
    trained_at:        Mapped[datetime]        = mapped_column(DateTime(timezone=True), server_default=func.now())
    deployed_at:       Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    retired_at:        Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    train_start_date:  Mapped[Optional[date]]  = mapped_column(Date)
    train_end_date:    Mapped[Optional[date]]  = mapped_column(Date)
    training_rows:     Mapped[Optional[int]]   = mapped_column(Integer)
    mae:               Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    mape:              Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    rmse:              Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    smape:             Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    r2:                Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 6))
    hyperparameters:   Mapped[Optional[dict]]  = mapped_column(JSONB)
    feature_names:     Mapped[Optional[list]]  = mapped_column(JSONB)
    feature_importance: Mapped[Optional[dict]] = mapped_column(JSONB)
    artifact_path:     Mapped[Optional[str]]   = mapped_column(Text)

    forecasts: Mapped[list["ForecastResult"]] = relationship(back_populates="model")


class FeatureStore(Base):
    __tablename__ = "feature_store"

    feature_date:           Mapped[date]              = mapped_column(Date,    primary_key=True)
    category_id:            Mapped[int]               = mapped_column(Integer, ForeignKey("dim_product_category.category_id"), primary_key=True)
    region_id:              Mapped[int]               = mapped_column(Integer, ForeignKey("dim_region.region_id"),             primary_key=True)
    total_revenue:          Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    total_quantity:         Mapped[Optional[int]]     = mapped_column(Integer)
    day_of_week:            Mapped[Optional[int]]     = mapped_column(Integer)
    day_of_month:           Mapped[Optional[int]]     = mapped_column(Integer)
    week_of_year:           Mapped[Optional[int]]     = mapped_column(Integer)
    month:                  Mapped[Optional[int]]     = mapped_column(Integer)
    quarter:                Mapped[Optional[int]]     = mapped_column(Integer)
    year:                   Mapped[Optional[int]]     = mapped_column(Integer)
    is_weekend:             Mapped[Optional[bool]]    = mapped_column(Boolean)
    is_month_start:         Mapped[Optional[bool]]    = mapped_column(Boolean)
    is_month_end:           Mapped[Optional[bool]]    = mapped_column(Boolean)
    is_quarter_start:       Mapped[Optional[bool]]    = mapped_column(Boolean)
    is_quarter_end:         Mapped[Optional[bool]]    = mapped_column(Boolean)
    is_public_holiday:      Mapped[Optional[bool]]    = mapped_column(Boolean)
    retail_season:          Mapped[Optional[str]]     = mapped_column(String(60))
    lag_7d:                 Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    lag_14d:                Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    lag_28d:                Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    lag_90d:                Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    lag_365d:               Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    rolling_mean_7d:        Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    rolling_mean_14d:       Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    rolling_mean_28d:       Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    rolling_std_7d:         Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    rolling_std_28d:        Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    rolling_min_28d:        Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    rolling_max_28d:        Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    yoy_revenue:            Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    yoy_growth_pct:         Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    revenue_trend_7d:       Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    revenue_trend_28d:      Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    active_promo_count:     Mapped[Optional[int]]     = mapped_column(Integer, default=0)
    max_discount_pct:       Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    daily_campaign_spend:   Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    competitor_price_index: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 3))
    avg_temp_c:             Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    precipitation_mm:       Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 2))
    is_extreme_weather:     Mapped[Optional[bool]]    = mapped_column(Boolean, default=False)
    median_income_usd:      Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    urban_pct:              Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    population:             Mapped[Optional[int]]     = mapped_column(BigInteger)
    computed_at:            Mapped[datetime]          = mapped_column(DateTime(timezone=True), server_default=func.now())


class ForecastResult(Base):
    __tablename__ = "forecast_results"
    __table_args__ = (
        UniqueConstraint("model_id", "segment_key", "forecast_date", name="uq_forecast"),
    )

    forecast_id:       Mapped[int]             = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    model_id:          Mapped[int]             = mapped_column(BigInteger, ForeignKey("model_registry.model_id"), nullable=False)
    model_name:        Mapped[str]             = mapped_column(String(80),  nullable=False)
    segment_key:       Mapped[str]             = mapped_column(String(120), nullable=False, index=True)
    category_id:       Mapped[Optional[int]]   = mapped_column(Integer, ForeignKey("dim_product_category.category_id"))
    region_id:         Mapped[Optional[int]]   = mapped_column(Integer, ForeignKey("dim_region.region_id"))
    forecast_date:     Mapped[date]            = mapped_column(Date, nullable=False, index=True)
    generated_at:      Mapped[datetime]        = mapped_column(DateTime(timezone=True), server_default=func.now())
    predicted_revenue: Mapped[Decimal]         = mapped_column(Numeric(14, 2), nullable=False)
    lower_80:          Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    upper_80:          Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    lower_95:          Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    upper_95:          Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    trend_component:   Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    seasonal_weekly:   Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    seasonal_yearly:   Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    holiday_component: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    actual_revenue:    Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    error_pct:         Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))

    model: Mapped["ModelRegistry"] = relationship(back_populates="forecasts")


class TrainingRun(Base):
    __tablename__ = "training_runs"

    run_id:            Mapped[int]             = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_key:           Mapped[str]             = mapped_column(String(40),  nullable=False, unique=True)
    triggered_by:      Mapped[str]             = mapped_column(String(80),  default="manual")
    status:            Mapped[str]             = mapped_column(String(20),  nullable=False, default="running")
    model_names:       Mapped[list]            = mapped_column(JSONB, nullable=False)
    horizon_days:      Mapped[int]             = mapped_column(Integer, nullable=False)
    started_at:        Mapped[datetime]        = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at:      Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_seconds:  Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    segments_total:    Mapped[int]             = mapped_column(Integer, default=0)
    segments_trained:  Mapped[int]             = mapped_column(Integer, default=0)
    segments_failed:   Mapped[int]             = mapped_column(Integer, default=0)
    segments_skipped:  Mapped[int]             = mapped_column(Integer, default=0)
    avg_mape:          Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    avg_mae:           Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    error_detail:      Mapped[Optional[str]]   = mapped_column(Text)
    run_metadata:      Mapped[Optional[dict]]  = mapped_column(JSONB)


class ForecastAccuracy(Base):
    __tablename__ = "forecast_accuracy"
    __table_args__ = (
        UniqueConstraint("model_id", "segment_key", "evaluation_date", "horizon_days", name="uq_accuracy"),
    )

    id:              Mapped[int]             = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    model_id:        Mapped[int]             = mapped_column(BigInteger, ForeignKey("model_registry.model_id"), nullable=False)
    segment_key:     Mapped[str]             = mapped_column(String(120), nullable=False)
    evaluation_date: Mapped[date]            = mapped_column(Date, nullable=False)
    horizon_days:    Mapped[int]             = mapped_column(Integer, nullable=False)
    mae:             Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    mape:            Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    rmse:            Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    bias:            Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    computed_at:     Mapped[datetime]         = mapped_column(DateTime(timezone=True), server_default=func.now())
