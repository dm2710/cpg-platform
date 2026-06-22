"""
Forecasting API — Phase 2 endpoints.

POST /forecasting/train              — trigger training pipeline
GET  /forecasting/runs               — list training runs
GET  /forecasting/runs/{run_id}      — single run detail

POST /forecasting/predict            — predict for one segment
POST /forecasting/predict/batch      — predict for all segments
GET  /forecasting/forecasts          — retrieve stored forecasts

GET  /forecasting/models             — list model registry
GET  /forecasting/models/{model_id}  — single model detail
POST /forecasting/models/backfill    — backfill actuals + compute accuracy
GET  /forecasting/models/compare     — compare models for a segment

GET  /forecasting/accuracy           — accuracy trend for a model
GET  /forecasting/features           — feature store query
"""

from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db, SessionLocal
from app.core.logging import get_logger
from app.schemas.base import _to_camel as _to_camel_key
from app.security.deps import CurrentUser, require_permission
from app.security.rbac import Permission
from app.forecasting.evaluation.metrics import backfill_actuals, compare_models
from app.forecasting.pipeline.predictor import (
    get_forecasts,
    predict_segment,
    run_prediction_pipeline,
)
from app.forecasting.training.trainer import run_training_pipeline
from app.schemas.base import MessageResponse, PaginatedResponse
from app.schemas.forecasting import (
    AccuracyTrendOut,
    BackfillRequest,
    BatchPredictionRequest,
    BatchPredictionResponse,
    FeatureStoreOut,
    ModelComparisonOut,
    ModelRegistryOut,
    PredictionRequest,
    PredictionResponse,
    ForecastPoint,
    TrainingRequest,
    TrainingRunListOut,
    TrainingRunOut,
)

router = APIRouter()
log    = get_logger(__name__)


# ── Training ──────────────────────────────────────────────

@router.post("/train", summary="Trigger model training pipeline")
def trigger_training(
    req:              TrainingRequest,
    background_tasks: BackgroundTasks,
    run_sync:         bool    = Query(default=False, description="Run synchronously (blocks until complete)"),
    db:               Session = Depends(get_db),
    user:             CurrentUser = Depends(require_permission(Permission.TRIGGER_FORECAST)),
):
    """
    Trains Prophet and/or LightGBM models for all segments.
    By default runs in the background; set run_sync=true for blocking execution.
    """
    if run_sync:
        result = run_training_pipeline(
            db=db,
            model_names=req.model_names,
            horizon_days=req.horizon_days,
            category_ids=req.category_ids,
            region_ids=req.region_ids,
            tune=req.tune,
            triggered_by=req.triggered_by,
        )
        # run_training_pipeline returns a raw dict with snake_case keys.
        # Convert to camelCase here so this endpoint's JSON contract
        # matches every other endpoint in the API (which serialize via
        # CamelBase's alias_generator). Without this, clients written
        # against the camelCase convention silently read `undefined`
        # for every field here.
        return {_to_camel_key(k): v for k, v in result.items()}

    def _bg_task():
        bg_db = SessionLocal()
        try:
            run_training_pipeline(
                db=bg_db,
                model_names=req.model_names,
                horizon_days=req.horizon_days,
                category_ids=req.category_ids,
                region_ids=req.region_ids,
                tune=req.tune,
                triggered_by="background",
            )
        finally:
            bg_db.close()

    background_tasks.add_task(_bg_task)
    return MessageResponse(message="Training pipeline started in background. Poll /forecasting/runs for status.")


@router.get("/runs", response_model=list[TrainingRunListOut], summary="List training runs")
def list_runs(
    status:    Optional[str] = None,
    limit:     int           = Query(default=20, ge=1, le=100),
    db:        Session       = Depends(get_db),
):
    filters = []
    params: dict = {"limit": limit}
    if status:
        filters.append("status = :status")
        params["status"] = status

    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    rows  = db.execute(
        text(f"""
            SELECT run_id, run_key, status, started_at,
                   avg_mape, segments_trained, segments_failed
            FROM training_runs
            {where}
            ORDER BY started_at DESC
            LIMIT :limit
        """),
        params,
    ).mappings().all()
    return [TrainingRunListOut(**dict(r)) for r in rows]


@router.get("/runs/{run_id}", response_model=TrainingRunOut, summary="Training run detail")
def get_run(run_id: int, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT * FROM training_runs WHERE run_id = :id"), {"id": run_id}
    ).mappings().first()
    if not row:
        raise HTTPException(404, f"Training run {run_id} not found")
    return TrainingRunOut(**dict(row))


# ── Prediction ────────────────────────────────────────────

@router.post("/predict", response_model=PredictionResponse, summary="Forecast one segment")
def predict_one(
    req: PredictionRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.TRIGGER_FORECAST)),
):
    """
    Generate a forecast for a single (category, region) combination.
    Uses the deployed model. Returns forecast rows with CI bands.
    """
    result = predict_segment(
        db=db,
        category_id=req.category_id,
        region_id=req.region_id,
        horizon_days=req.horizon_days,
        model_name=req.model_name,
    )

    status = result.get("status")
    if status == "no_model":
        raise HTTPException(404, detail=result["reason"])
    if status in ("no_data", "insufficient_data", "empty_forecast", "error"):
        raise HTTPException(422, detail=f"Cannot generate forecast: {status} — {result.get('error', result.get('reason', ''))}")

    from app.forecasting.features.engineer import segment_key as mk_seg
    seg = mk_seg(req.category_id, req.region_id)

    forecasts = []
    for row in result.get("forecasts", []):
        ds = row.get("ds")
        if hasattr(ds, "date"):
            ds = ds.date()
        forecasts.append(ForecastPoint(
            forecast_date=ds,
            model_name=result["model_name"],
            predicted_revenue=row.get("predicted_revenue", 0),
            lower_80=row.get("lower_80"),
            upper_80=row.get("upper_80"),
            lower_95=row.get("lower_95"),
            upper_95=row.get("upper_95"),
            trend_component=row.get("component_trend"),
            seasonal_weekly=row.get("component_weekly"),
            seasonal_yearly=row.get("component_yearly"),
            actual_revenue=None,
            error_pct=None,
            generated_at=datetime.now(timezone.utc),
        ))

    return PredictionResponse(
        segment_key=seg,
        category_id=req.category_id,
        region_id=req.region_id,
        model_name=result.get("model_name"),
        horizon_days=req.horizon_days,
        forecasts=forecasts,
        generated_at=datetime.now(timezone.utc),
    )


@router.post("/predict/batch", response_model=BatchPredictionResponse, summary="Forecast all segments")
def predict_batch(
    req:              BatchPredictionRequest,
    background_tasks: BackgroundTasks,
    run_sync:         bool    = Query(default=False),
    db:               Session = Depends(get_db),
    user:             CurrentUser = Depends(require_permission(Permission.TRIGGER_FORECAST)),
):
    if run_sync:
        result = run_prediction_pipeline(
            db=db,
            horizon_days=req.horizon_days,
            category_ids=req.category_ids,
            region_ids=req.region_ids,
            model_name=req.model_name,
        )
        return BatchPredictionResponse(**result)

    def _bg():
        bg_db = SessionLocal()
        try:
            run_prediction_pipeline(
                db=bg_db,
                horizon_days=req.horizon_days,
                category_ids=req.category_ids,
                region_ids=req.region_ids,
                model_name=req.model_name,
            )
        finally:
            bg_db.close()

    background_tasks.add_task(_bg)
    return MessageResponse(message="Batch prediction started in background.")


@router.get("/forecasts", response_model=list[ForecastPoint], summary="Retrieve stored forecasts")
def retrieve_forecasts(
    category_id: Optional[int]  = None,
    region_id:   Optional[int]  = None,
    start_date:  Optional[date] = None,
    end_date:    Optional[date] = None,
    model_name:  Optional[str]  = None,
    db:          Session        = Depends(get_db),
):
    rows = get_forecasts(db, category_id, region_id, start_date, end_date, model_name)

    # If exact segment has no forecasts, fall back to the global segment so the
    # chart always shows something after training + predicting.
    if not rows and (category_id is not None or region_id is not None):
        rows = get_forecasts(db, None, None, start_date, end_date, model_name)

    # Deduplicate by forecast_date — keep only the most recently generated row
    # per date (multiple training runs write separate model_id rows).
    seen: dict = {}
    for r in rows:
        fd = r["forecast_date"]
        if fd not in seen or r.get("generated_at", "") > seen[fd].get("generated_at", ""):
            seen[fd] = r
    rows = sorted(seen.values(), key=lambda x: x["forecast_date"])

    return [ForecastPoint(**r) for r in rows]


# ── Model registry ────────────────────────────────────────

@router.get("/models", response_model=list[ModelRegistryOut], summary="List all models")
def list_models(
    status:      Optional[str] = None,
    model_name:  Optional[str] = None,
    segment_key: Optional[str] = None,
    limit:       int           = Query(default=50, ge=1, le=500),
    db:          Session       = Depends(get_db),
):
    filters = []
    params: dict = {"limit": limit}
    if status:
        filters.append("status = :status")
        params["status"] = status
    if model_name:
        filters.append("model_name = :model_name")
        params["model_name"] = model_name
    if segment_key:
        filters.append("segment_key = :seg")
        params["seg"] = segment_key

    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    rows  = db.execute(
        text(f"SELECT * FROM model_registry {where} ORDER BY trained_at DESC LIMIT :limit"),
        params,
    ).mappings().all()
    return [ModelRegistryOut(**dict(r)) for r in rows]


@router.post("/models/backfill", response_model=dict, summary="Backfill actuals onto forecasts")
def run_backfill(
    req: BackfillRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.TRIGGER_FORECAST)),
):
    return backfill_actuals(db, req.model_id, req.segment_key, req.as_of_date)


@router.get("/models/compare", response_model=list[ModelComparisonOut])
def model_comparison(
    segment_key:  str,
    horizon_days: int     = Query(default=30, ge=1, le=365),
    db:           Session = Depends(get_db),
):
    rows = compare_models(db, segment_key, horizon_days)
    return [ModelComparisonOut(**r) for r in rows]


# NOTE: dynamic route /models/{model_id} must come AFTER static routes
# /models/backfill and /models/compare, otherwise FastAPI matches those
# path segments as model_id values.
@router.get("/models/{model_id}", response_model=ModelRegistryOut)
def get_model(model_id: int, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT * FROM model_registry WHERE model_id = :id"), {"id": model_id}
    ).mappings().first()
    if not row:
        raise HTTPException(404, f"Model {model_id} not found")
    return ModelRegistryOut(**dict(row))


# ── Accuracy ──────────────────────────────────────────────

@router.get("/accuracy", response_model=list[AccuracyTrendOut])
def accuracy_trend(
    model_id:    int,
    segment_key: str,
    horizon_days: Optional[int] = None,
    db:          Session        = Depends(get_db),
):
    filters = ["model_id = :mid", "segment_key = :seg"]
    params: dict = {"mid": model_id, "seg": segment_key}
    if horizon_days:
        filters.append("horizon_days = :horizon")
        params["horizon"] = horizon_days

    rows = db.execute(
        text(f"""
            SELECT evaluation_date, horizon_days, mape, mae, bias
            FROM forecast_accuracy
            WHERE {' AND '.join(filters)}
            ORDER BY evaluation_date DESC
        """),
        params,
    ).mappings().all()
    return [AccuracyTrendOut(**dict(r)) for r in rows]


# ── Feature store ─────────────────────────────────────────

@router.get("/features", response_model=list[FeatureStoreOut], summary="Query feature store")
def query_feature_store(
    category_id: Optional[int]  = None,
    region_id:   Optional[int]  = None,
    start_date:  Optional[date] = None,
    end_date:    Optional[date] = None,
    limit:       int            = Query(default=90, ge=1, le=1000),
    db:          Session        = Depends(get_db),
):
    filters = []
    params: dict = {"limit": limit}
    if category_id is not None:
        filters.append("category_id = :cat")
        params["cat"] = category_id
    if region_id is not None:
        filters.append("region_id = :region")
        params["region"] = region_id
    if start_date:
        filters.append("feature_date >= :start")
        params["start"] = start_date
    if end_date:
        filters.append("feature_date <= :end")
        params["end"] = end_date

    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    rows  = db.execute(
        text(f"""
            SELECT feature_date, category_id, region_id, total_revenue,
                   lag_7d, lag_28d, rolling_mean_28d, yoy_growth_pct,
                   active_promo_count, max_discount_pct,
                   is_public_holiday, retail_season, computed_at
            FROM feature_store
            {where}
            ORDER BY feature_date DESC
            LIMIT :limit
        """),
        params,
    ).mappings().all()
    return [FeatureStoreOut(**dict(r)) for r in rows]
