"""
Pydantic schemas for the forecasting API.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import Field, field_validator

from app.schemas.base import CamelBase


# ── Training ──────────────────────────────────────────────

class TrainingRequest(CamelBase):
    model_names:  list[str]       = Field(default=["lightgbm"])
    horizon_days: int             = Field(default=30, ge=1, le=365)
    category_ids: Optional[list[int]] = None
    region_ids:   Optional[list[int]] = None
    tune:         bool            = Field(default=False, description="Run Optuna hyperparameter search")
    triggered_by: str             = Field(default="api")

    @field_validator("model_names")
    @classmethod
    def validate_models(cls, v):
        # Filter out prophet silently — it requires CmdStan which is not installed
        v = [m for m in v if m != "prophet"]
        if not v:
            v = ["lightgbm"]
        allowed = {"lightgbm"}
        invalid = set(v) - allowed
        if invalid:
            raise ValueError(f"Unknown models: {invalid}. Allowed: {allowed}")
        return v


class TrainingRunOut(CamelBase):
    run_id:            int
    run_key:           str
    status:            str
    model_names:       list[str]
    horizon_days:      int
    segments_total:    int
    segments_trained:  int
    segments_failed:   int
    segments_skipped:  int
    avg_mape:          Optional[float]
    avg_mae:           Optional[float]
    duration_seconds:  Optional[float]
    started_at:        datetime
    completed_at:      Optional[datetime]


class TrainingRunListOut(CamelBase):
    run_id:       int
    run_key:      str
    status:       str
    started_at:   datetime
    avg_mape:     Optional[float]
    segments_trained: int
    segments_failed:  int


# ── Prediction ────────────────────────────────────────────

class PredictionRequest(CamelBase):
    category_id:  Optional[int] = None
    region_id:    Optional[int] = None
    horizon_days: int           = Field(default=30, ge=1, le=365)
    model_name:   Optional[str] = None


class BatchPredictionRequest(CamelBase):
    horizon_days: int               = Field(default=30, ge=1, le=365)
    category_ids: Optional[list[int]] = None
    region_ids:   Optional[list[int]] = None
    model_name:   Optional[str]     = None


class ForecastPoint(CamelBase):
    forecast_date:     date
    model_name:        str
    predicted_revenue: Decimal
    lower_80:          Optional[Decimal]
    upper_80:          Optional[Decimal]
    lower_95:          Optional[Decimal]
    upper_95:          Optional[Decimal]
    trend_component:   Optional[Decimal]
    seasonal_weekly:   Optional[Decimal]
    seasonal_yearly:   Optional[Decimal]
    actual_revenue:    Optional[Decimal]
    error_pct:         Optional[Decimal]
    generated_at:      Optional[datetime]


class PredictionResponse(CamelBase):
    segment_key:   str
    category_id:   Optional[int]
    region_id:     Optional[int]
    model_name:    Optional[str]
    horizon_days:  int
    forecasts:     list[ForecastPoint]
    generated_at:  datetime


class BatchPredictionResponse(CamelBase):
    segments_total:    int
    segments_forecast: int
    segments_no_model: int
    segments_failed:   int
    horizon_days:      int


# ── Model registry ────────────────────────────────────────

class ModelRegistryOut(CamelBase):
    model_id:        int
    model_name:      str
    model_version:   str
    segment_key:     str
    category_id:     Optional[int]
    region_id:       Optional[int]
    status:          str
    trained_at:      datetime
    deployed_at:     Optional[datetime]
    train_start_date: Optional[date]
    train_end_date:   Optional[date]
    training_rows:   Optional[int]
    mae:             Optional[Decimal]
    mape:            Optional[Decimal]
    rmse:            Optional[Decimal]
    r2:              Optional[Decimal]
    hyperparameters: Optional[dict]
    feature_names:   Optional[list]
    feature_importance: Optional[dict]


# ── Evaluation ────────────────────────────────────────────

class BackfillRequest(CamelBase):
    model_id:    int
    segment_key: str
    as_of_date:  Optional[date] = None


class ModelComparisonOut(CamelBase):
    model_id:     int
    model_name:   str
    model_version: str
    mape:         Optional[Decimal]
    mae:          Optional[Decimal]
    recent_mape:  Optional[Decimal]
    recent_mae:   Optional[Decimal]
    eval_points:  Optional[int]


class AccuracyTrendOut(CamelBase):
    evaluation_date: date
    horizon_days:    int
    mape:            Optional[Decimal]
    mae:             Optional[Decimal]
    bias:            Optional[Decimal]


# ── Feature store ─────────────────────────────────────────

class FeatureStoreOut(CamelBase):
    feature_date:       date
    category_id:        int
    region_id:          int
    total_revenue:      Optional[Decimal]
    lag_7d:             Optional[Decimal]
    lag_28d:            Optional[Decimal]
    rolling_mean_28d:   Optional[Decimal]
    yoy_growth_pct:     Optional[Decimal]
    active_promo_count: Optional[int]
    max_discount_pct:   Optional[Decimal]
    is_public_holiday:  Optional[bool]
    retail_season:      Optional[str]
    computed_at:        datetime
