"""
Prediction pipeline.

For each segment:
  1. Find the deployed model (best by hold-out MAPE)
  2. Rebuild the feature matrix (history + future skeleton)
  3. Run predict() on future rows only
  4. Write results to forecast_results (upsert)
  5. Return a structured ForecastResponse

The prediction pipeline is stateless — it always re-derives features
from the live database, so late-arriving actuals are automatically
incorporated into the lag/rolling features for the next run.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timezone, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.forecasting.features.engineer import (
    build_feature_matrix,
    segment_key,
)
from app.forecasting.models.lightgbm_model import LightGBMForecaster
from app.forecasting.models.prophet_model import ProphetForecaster
from app.forecasting.models.base import ForecastOutput

log = get_logger(__name__)


def get_deployed_model_info(
    db: Session,
    category_id: Optional[int],
    region_id: Optional[int],
    model_name: Optional[str] = None,
) -> Optional[dict]:
    """
    Fetch the deployed model row from model_registry for a segment.

    Falls back through a hierarchy so that a successful training run is always
    usable, even when the exact (category, region) combination was skipped
    due to insufficient data:
      1. Exact segment match  (cat=X|region=Y)
      2. Same category, any region  (cat=X|region=all)
      3. Any category, same region  (cat=all|region=Y)
      4. Global model  (segment_key = 'global')
      5. Any deployed model  (last resort)

    The returned dict always includes category_id and region_id so the
    caller knows which segment's data was used to train the model.
    """
    model_filter = "AND model_name = :model_name" if model_name else ""
    base_params: dict = {}
    if model_name:
        base_params["model_name"] = model_name

    def _query(seg: str) -> Optional[dict]:
        params = {**base_params, "seg": seg}
        row = db.execute(
            text(f"""
                SELECT model_id, model_name, model_version, mape, mae,
                       category_id, region_id,
                       train_start_date, train_end_date, hyperparameters,
                       feature_names, trained_at
                FROM model_registry
                WHERE segment_key = :seg
                  AND status = 'deployed'
                  {model_filter}
                ORDER BY mape ASC NULLS LAST
                LIMIT 1
            """),
            params,
        ).mappings().first()
        return dict(row) if row else None

    # 1. Exact segment
    row = _query(segment_key(category_id, region_id))
    if row:
        return row

    # 2. Same category, all regions
    if category_id is not None:
        row = _query(segment_key(category_id, None))
        if row:
            return row

    # 3. All categories, same region
    if region_id is not None:
        row = _query(segment_key(None, region_id))
        if row:
            return row

    # 4. Global model
    row = _query("global")
    if row:
        return row

    # 5. Any deployed model (last resort)
    any_row = db.execute(
        text(f"""
            SELECT model_id, model_name, model_version, mape, mae,
                   category_id, region_id,
                   train_start_date, train_end_date, hyperparameters,
                   feature_names, trained_at
            FROM model_registry
            WHERE status = 'deployed'
              {model_filter}
            ORDER BY mape ASC NULLS LAST
            LIMIT 1
        """),
        base_params,
    ).mappings().first()
    return dict(any_row) if any_row else None


def _instantiate_model(model_info: dict):
    """Reconstruct a model instance from registry metadata."""
    name   = model_info["model_name"]
    params = model_info.get("hyperparameters") or {}

    # Strip non-constructor params stored in hyperparameters
    if name == "prophet":
        constructor_keys = {
            "seasonality_mode", "yearly_seasonality", "weekly_seasonality",
            "daily_seasonality", "changepoint_prior_scale",
            "seasonality_prior_scale", "holidays_prior_scale",
        }
        clean_params = {k: v for k, v in params.items() if k in constructor_keys}
        return ProphetForecaster(params=clean_params)

    if name == "lightgbm":
        from app.forecasting.models.lightgbm_model import DEFAULT_PARAMS
        clean_params = {k: v for k, v in params.items()
                        if k in DEFAULT_PARAMS and k not in ("feature_importance",)}
        # Convert stringified numerics back
        for k in clean_params:
            try:
                clean_params[k] = float(clean_params[k]) if "." in str(clean_params[k]) else int(clean_params[k])
            except (ValueError, TypeError):
                pass
        return LightGBMForecaster(params=clean_params)

    raise ValueError(f"Unknown model name: {name}")


def predict_segment(
    db: Session,
    category_id: Optional[int],
    region_id: Optional[int],
    horizon_days: int = 30,
    model_name: Optional[str] = None,
) -> dict:
    """
    Generate forecasts for one segment and persist to forecast_results.

    When the exact (category, region) segment has too few rows to re-train on,
    we use the broader segment that the fallback model was originally trained on
    (e.g. global) to fit the model, then write the forecast under the *requested*
    segment key so the UI can always find it.
    """
    seg_key = segment_key(category_id, region_id)

    # Find deployed model — may be from a broader/fallback segment
    model_info = get_deployed_model_info(db, category_id, region_id, model_name)
    if model_info is None:
        return {
            "segment_key": seg_key,
            "status": "no_model",
            "reason": "No deployed model found for this segment. Run training first.",
        }

    model_id = model_info["model_id"]
    model_nm = model_info["model_name"]

    # The segment the fallback model was trained on (may differ from requested)
    model_cat_id = model_info.get("category_id")
    model_reg_id = model_info.get("region_id")
    is_fallback  = (model_cat_id != category_id or model_reg_id != region_id)

    # --- Build feature data for the REQUESTED segment ---
    df_requested = build_feature_matrix(
        db=db,
        category_id=category_id,
        region_id=region_id,
        include_future=True,
        horizon_days=horizon_days,
    )

    train_rows = len(df_requested[df_requested["y"].notna()]) if not df_requested.empty else 0

    if is_fallback and train_rows < 30:
        # The requested segment is too sparse to re-train on.
        # Use the model's own (broader) segment for training data instead.
        log.info(
            "prediction.sparse_segment_fallback",
            requested=seg_key,
            model_segment=segment_key(model_cat_id, model_reg_id),
            rows=train_rows,
        )
        df_train_src = build_feature_matrix(
            db=db,
            category_id=model_cat_id,
            region_id=model_reg_id,
            include_future=False,
        )
        # Future skeleton: use requested segment dates if available, else fall back
        future_df = (
            df_requested[df_requested["y"].isna()].copy()
            if not df_requested.empty
            else pd.DataFrame()
        )
        if future_df.empty:
            df_fallback_full = build_feature_matrix(
                db=db,
                category_id=model_cat_id,
                region_id=model_reg_id,
                include_future=True,
                horizon_days=horizon_days,
            )
            future_df = df_fallback_full[df_fallback_full["y"].isna()].copy()

        if df_train_src.empty:
            return {"segment_key": seg_key, "status": "no_data"}

        train_df = df_train_src[df_train_src["y"].notna()].copy()
        df_full  = pd.concat([train_df, future_df], ignore_index=True).sort_values("ds")
    else:
        if df_requested.empty:
            return {"segment_key": seg_key, "status": "no_data"}
        df_full   = df_requested
        train_df  = df_full[df_full["y"].notna()].copy()
        future_df = df_full[df_full["y"].isna()].copy()

    if len(train_df) < 30:
        return {"segment_key": seg_key, "status": "insufficient_data", "rows": len(train_df)}

    if future_df.empty:
        return {"segment_key": seg_key, "status": "no_future_rows"}

    model = _instantiate_model(model_info)
    model.fit(train_df)
    output = model.predict(df_full)

    # Keep only future rows in the output
    output_df = output.to_dataframe()
    output_df = output_df.merge(
        future_df[["ds"]].assign(_is_future=True), on="ds", how="inner"
    ).drop(columns=["_is_future"])

    if output_df.empty:
        return {"segment_key": seg_key, "status": "empty_forecast"}

    # Always write under the REQUESTED segment key so the UI can find it
    _write_forecast_results(db, output_df, model_id, model_nm, seg_key, category_id, region_id)

    forecast_rows = output_df.to_dict(orient="records")

    log.info(
        "prediction.complete",
        segment=seg_key,
        model=model_nm,
        horizon=horizon_days,
        rows=len(forecast_rows),
        used_fallback=is_fallback,
    )

    return {
        "segment_key":  seg_key,
        "status":       "ok",
        "model_id":     model_id,
        "model_name":   model_nm,
        "horizon_days": horizon_days,
        "forecast_rows": len(forecast_rows),
        "forecasts":    forecast_rows,
    }


def run_prediction_pipeline(
    db: Session,
    horizon_days: int = 30,
    category_ids: Optional[list[int]] = None,
    region_ids: Optional[list[int]] = None,
    model_name: Optional[str] = None,
) -> dict:
    """
    Generate forecasts for all (or filtered) segments.
    """
    from app.forecasting.features.engineer import get_segments

    segments = get_segments(db)
    if category_ids:
        segments = [s for s in segments if s[0] is None or s[0] in category_ids]
    if region_ids:
        segments = [s for s in segments if s[1] is None or s[1] in region_ids]

    results   = []
    succeeded = failed = no_model = 0

    for cat_id, reg_id in segments:
        # Cast to plain Python int — psycopg2 can't adapt numpy.int32
        cat_id = int(cat_id) if cat_id is not None else None
        reg_id = int(reg_id) if reg_id is not None else None
        try:
            result = predict_segment(db, cat_id, reg_id, horizon_days, model_name)
            results.append(result)
            if result["status"] == "ok":
                succeeded += 1
            elif result["status"] == "no_model":
                no_model += 1
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            seg = segment_key(cat_id, reg_id)
            log.error("prediction.failed", segment=seg, error=str(exc))
            results.append({"segment_key": seg, "status": "error", "error": str(exc)})

    return {
        "segments_total":    len(segments),
        "segments_forecast": succeeded,
        "segments_no_model": no_model,
        "segments_failed":   failed,
        "horizon_days":      horizon_days,
        "results":           results,
    }


def get_forecasts(
    db: Session,
    category_id: Optional[int] = None,
    region_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    model_name: Optional[str] = None,
) -> list[dict]:
    """Retrieve persisted forecasts from forecast_results."""
    seg = segment_key(category_id, region_id)
    filters = ["fr.segment_key = :seg"]
    params: dict = {"seg": seg}

    if start_date:
        filters.append("fr.forecast_date >= :start")
        params["start"] = start_date
    if end_date:
        filters.append("fr.forecast_date <= :end")
        params["end"] = end_date
    if model_name:
        filters.append("fr.model_name = :model_name")
        params["model_name"] = model_name

    rows = db.execute(
        text(f"""
            SELECT fr.forecast_date, fr.model_name, fr.predicted_revenue,
                   fr.lower_80, fr.upper_80, fr.lower_95, fr.upper_95,
                   fr.trend_component, fr.seasonal_weekly, fr.seasonal_yearly,
                   fr.actual_revenue, fr.error_pct, fr.generated_at
            FROM forecast_results fr
            WHERE {' AND '.join(filters)}
            ORDER BY fr.forecast_date, fr.model_name
        """),
        params,
    ).mappings().all()

    return [dict(r) for r in rows]


def _write_forecast_results(
    db: Session,
    df: pd.DataFrame,
    model_id: int,
    model_name: str,
    seg_key: str,
    category_id: Optional[int],
    region_id: Optional[int],
) -> None:
    for _, row in df.iterrows():
        db.execute(
            text("""
                INSERT INTO forecast_results
                    (model_id, model_name, segment_key, category_id, region_id,
                     forecast_date, predicted_revenue,
                     lower_80, upper_80, lower_95, upper_95,
                     trend_component, seasonal_weekly, seasonal_yearly)
                VALUES
                    (:mid, :mname, :seg, :cat, :region,
                     :fdate, :yhat,
                     :l80, :u80, :l95, :u95,
                     :trend, :s_weekly, :s_yearly)
                ON CONFLICT (model_id, segment_key, forecast_date) DO UPDATE SET
                    predicted_revenue = EXCLUDED.predicted_revenue,
                    lower_80          = EXCLUDED.lower_80,
                    upper_80          = EXCLUDED.upper_80,
                    lower_95          = EXCLUDED.lower_95,
                    upper_95          = EXCLUDED.upper_95,
                    generated_at      = now()
            """),
            {
                "mid":      model_id,
                "mname":    model_name,
                "seg":      seg_key,
                "cat":      category_id,
                "region":   region_id,
                "fdate":    row["ds"].date() if hasattr(row["ds"], "date") else row["ds"],
                "yhat":     float(row["predicted_revenue"]),
                "l80":      _safe_float(row.get("lower_80")),
                "u80":      _safe_float(row.get("upper_80")),
                "l95":      _safe_float(row.get("lower_95")),
                "u95":      _safe_float(row.get("upper_95")),
                "trend":    _safe_float(row.get("component_trend")),
                "s_weekly": _safe_float(row.get("component_weekly")),
                "s_yearly": _safe_float(row.get("component_yearly")),
            },
        )
    db.commit()


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        v = float(val)
        return None if np.isnan(v) else v
    except (TypeError, ValueError):
        return None
