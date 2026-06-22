"""
Training pipeline.

Orchestrates per-segment model training:
  1. Build feature matrix from agg_revenue_daily + all signals
  2. Split into train / hold-out (last horizon_days)
  3. Walk-forward CV for robust metric estimation
  4. Final fit on full training window
  5. Evaluate on hold-out
  6. Register model in model_registry with metrics
  7. Write feature matrix to feature_store for audit / debugging

Supports Prophet and LightGBM. Both are trained per segment; the
best model per segment (by hold-out MAPE) is marked as 'deployed'.
"""

from __future__ import annotations

import copy
import json
import time
from datetime import date, datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.forecasting.evaluation.metrics import walk_forward_cv, evaluate_on_holdout
from app.forecasting.features.engineer import (
    build_feature_matrix,
    get_segments,
    persist_feature_store,
    segment_key,
)
from app.forecasting.models.lightgbm_model import LightGBMForecaster
from app.forecasting.models.prophet_model import ProphetForecaster

log      = get_logger(__name__)
settings = get_settings()

MIN_TRAIN_ROWS       = 30   # minimum daily rows needed before training a segment
HOLDOUT_DAYS         = 30   # days reserved for hold-out evaluation


def _build_run_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _get_model(model_name: str, tune: bool = False) -> object:
    if model_name == "lightgbm":
        return LightGBMForecaster(tune=tune)
    if model_name == "prophet":
        raise ValueError(
            "Prophet is disabled — CmdStan is not installed in this environment. "
            "Use model_names=['lightgbm'] instead."
        )
    raise ValueError(f"Unknown model: {model_name}")


def train_segment(
    db: Session,
    category_id: Optional[int],
    region_id: Optional[int],
    model_names: list[str],
    horizon_days: int,
    run_key: str,
    tune: bool = False,
) -> dict:
    """
    Train all requested models for a single (category, region) segment.
    Returns a summary dict for this segment.
    """
    seg_key = segment_key(category_id, region_id)
    t0 = time.time()

    # ── Build feature matrix ───────────────────────────────
    df = build_feature_matrix(
        db=db,
        category_id=category_id,
        region_id=region_id,
    )

    actual_rows = df["y"].notna().sum() if not df.empty else 0

    if actual_rows < MIN_TRAIN_ROWS:
        # Specific segment has too few rows. Train on a broader segment's data
        # but register the model under the ORIGINAL requested segment key so the
        # predictor can find it without needing a fallback chain.
        original_seg_key = seg_key
        fallback_cat = category_id   # try category-wide first (same cat, all regions)
        fallback_reg = None
        df_fallback = build_feature_matrix(db=db, category_id=fallback_cat, region_id=fallback_reg)
        fallback_rows = df_fallback["y"].notna().sum() if not df_fallback.empty else 0

        if fallback_rows < MIN_TRAIN_ROWS:
            # Try global (all categories, all regions)
            df_fallback = build_feature_matrix(db=db, category_id=None, region_id=None)
            fallback_rows = df_fallback["y"].notna().sum() if not df_fallback.empty else 0
            fallback_cat = None
            fallback_reg = None

        if fallback_rows >= MIN_TRAIN_ROWS:
            log.info(
                "training.sparse_fallback",
                segment=original_seg_key,
                rows=actual_rows,
                fallback=segment_key(fallback_cat, fallback_reg),
                fallback_rows=fallback_rows,
            )
            df = df_fallback
            # Keep category_id/region_id/seg_key as the ORIGINAL requested values
            # so _register_model stores the model under the correct segment key.
            # The model will be trained on broader data but looked up by the
            # specific segment the user requested.
        else:
            log.info("training.skipped", segment=seg_key, rows=actual_rows)
            return {"segment_key": seg_key, "status": "skipped",
                    "reason": f"Insufficient data ({actual_rows} rows) and no broader segment available"}

    # Persist features to feature_store
    if category_id is not None and region_id is not None:
        persist_feature_store(df[df["y"].notna()], category_id, region_id, db)

    # ── Train / hold-out split ─────────────────────────────
    df_known  = df[df["y"].notna()].copy()
    n_holdout = min(HOLDOUT_DAYS, len(df_known) // 5)  # 20% or 30d max
    train_df  = df_known.iloc[:-n_holdout]
    holdout_df = df_known.iloc[-n_holdout:]

    results = {"segment_key": seg_key, "status": "ok", "models": {}}
    best_mape  = float("inf")
    best_model_id: Optional[int] = None

    for model_name in model_names:
        log.info("training.model_start", segment=seg_key, model=model_name)
        try:
            model = _get_model(model_name, tune=tune)

            # Walk-forward CV
            cv_result = walk_forward_cv(
                copy.deepcopy(model),
                train_df,
                horizon_days=min(horizon_days, 14),
                n_splits=3,
            )

            # Final fit on full training set
            model.fit(train_df)

            # Hold-out evaluation
            holdout_output  = model.predict(holdout_df)
            from app.forecasting.models.base import compute_metrics
            holdout_metrics = compute_metrics(
                holdout_df["y"].values,
                holdout_output.predicted_revenue[:len(holdout_df)],
            )

            # Register in model_registry
            version   = f"{run_key}_{model_name}"
            model_id  = _register_model(
                db=db,
                model_name=model_name,
                model_version=version,
                category_id=category_id,
                region_id=region_id,
                seg_key=seg_key,
                train_df=train_df,
                metrics=holdout_metrics,
                params=model.get_params(),
                feature_names=model.feature_names_,
            )

            results["models"][model_name] = {
                "model_id":  model_id,
                "cv":        cv_result,
                "holdout":   holdout_metrics.to_dict(),
                "status":    "ok",
            }

            if holdout_metrics.mape < best_mape:
                best_mape     = holdout_metrics.mape
                best_model_id = model_id

            log.info(
                "training.model_complete",
                segment=seg_key, model=model_name,
                mape=holdout_metrics.mape, mae=holdout_metrics.mae,
            )

        except Exception as exc:
            log.error("training.model_failed", segment=seg_key, model=model_name, error=str(exc))
            results["models"][model_name] = {"status": "failed", "error": str(exc)}

    # Mark best model as 'deployed'
    if best_model_id is not None:
        # Un-deploy any previously-deployed model for this segment first so there
        # is always exactly one deployed model per segment.
        db.execute(
            text("""
                UPDATE model_registry
                   SET status = 'archived'
                 WHERE segment_key = :seg
                   AND status = 'deployed'
                   AND model_id <> :mid
            """),
            {"seg": seg_key, "mid": best_model_id},
        )
        db.execute(
            text("""
                UPDATE model_registry SET status = 'deployed', deployed_at = now()
                WHERE model_id = :mid
            """),
            {"mid": best_model_id},
        )
        db.commit()
        results["best_model_id"] = best_model_id
    else:
        # All model fits failed — mark segment as failed so callers don't count
        # it as successfully trained.
        results["status"] = "failed"
        results["error"] = "All model fits failed; see per-model errors above."

    results["duration_s"] = round(time.time() - t0, 2)
    return results


def run_training_pipeline(
    db: Session,
    model_names: Optional[list[str]] = None,
    horizon_days: int = 30,
    category_ids: Optional[list[int]] = None,
    region_ids: Optional[list[int]] = None,
    tune: bool = False,
    triggered_by: str = "manual",
) -> dict:
    """
    Full training run across all (or filtered) segments.
    Creates a training_runs row and updates it on completion.
    """
    model_names = [m for m in (model_names or ["lightgbm"]) if m != "prophet"]
    if not model_names:
        model_names = ["lightgbm"]
    run_key     = _build_run_key()
    started_at  = datetime.now(timezone.utc)

    # Create run record
    run_id = db.execute(
        text("""
            INSERT INTO training_runs
                (run_key, triggered_by, status, model_names, horizon_days)
            VALUES (:key, :by, 'running', CAST(:models AS jsonb), :horizon)
            RETURNING run_id
        """),
        {
            "key":     run_key,
            "by":      triggered_by,
            "models":  json.dumps(model_names),
            "horizon": horizon_days,
        },
    ).scalar()
    db.commit()

    segments = get_segments(db)

    # Filter if requested
    if category_ids:
        segments = [s for s in segments if s[0] is None or s[0] in category_ids]
    if region_ids:
        segments = [s for s in segments if s[1] is None or s[1] in region_ids]

    log.info("pipeline.start", run_key=run_key, segments=len(segments), models=model_names)

    total = trained = failed = skipped = 0
    all_mapes: list[float] = []
    all_maes:  list[float] = []
    segment_results: list[dict] = []

    # Update total count
    db.execute(
        text("UPDATE training_runs SET segments_total = :n WHERE run_id = :id"),
        {"n": len(segments), "id": run_id},
    )
    db.commit()

    for cat_id, reg_id in segments:
        cat_id = int(cat_id) if cat_id is not None else None
        reg_id = int(reg_id) if reg_id is not None else None
        total += 1
        try:
            result = train_segment(
                db=db,
                category_id=cat_id,
                region_id=reg_id,
                model_names=model_names,
                horizon_days=horizon_days,
                run_key=run_key,
                tune=tune,
            )
            segment_results.append(result)

            if result["status"] == "skipped":
                skipped += 1
            elif result["status"] == "failed":
                failed += 1
            else:
                trained += 1
                # Collect metrics from best model
                for mn, mr in result.get("models", {}).items():
                    if mr.get("status") == "ok" and "holdout" in mr:
                        all_mapes.append(mr["holdout"]["mape"])
                        all_maes.append(mr["holdout"]["mae"])

        except Exception as exc:
            failed += 1
            log.error("pipeline.segment_failed", cat=cat_id, region=reg_id, error=str(exc))
            segment_results.append({
                "segment_key": segment_key(cat_id, reg_id),
                "status": "failed",
                "error": str(exc),
            })

    # Finalise run record
    duration = (datetime.now(timezone.utc) - started_at).total_seconds()
    avg_mape  = float(sum(all_mapes) / len(all_mapes)) if all_mapes else None
    avg_mae   = float(sum(all_maes)  / len(all_maes))  if all_maes  else None

    db.execute(
        text("""
            UPDATE training_runs SET
                status            = :status,
                completed_at      = now(),
                duration_seconds  = :dur,
                segments_trained  = :trained,
                segments_failed   = :failed,
                segments_skipped  = :skipped,
                avg_mape          = :mape,
                avg_mae           = :mae
            WHERE run_id = :id
        """),
        {
            "status":  "completed" if failed == 0 else "partial",
            "dur":     duration,
            "trained": trained,
            "failed":  failed,
            "skipped": skipped,
            "mape":    avg_mape,
            "mae":     avg_mae,
            "id":      run_id,
        },
    )
    db.commit()

    summary = {
        "run_id":           run_id,
        "run_key":          run_key,
        "status":           "completed" if failed == 0 else "partial",
        "segments_total":   total,
        "segments_trained": trained,
        "segments_failed":  failed,
        "segments_skipped": skipped,
        "avg_mape":         avg_mape,
        "avg_mae":          avg_mae,
        "duration_seconds": round(duration, 2),
        "model_names":      model_names,
    }

    log.info("pipeline.complete", **summary)
    return summary


def _register_model(
    db: Session,
    model_name: str,
    model_version: str,
    category_id: Optional[int],
    region_id: Optional[int],
    seg_key: str,
    train_df,
    metrics,
    params: dict,
    feature_names: list[str],
) -> int:
    feature_imp = params.pop("feature_importance", None)

    model_id = db.execute(
        text("""
            INSERT INTO model_registry
                (model_name, model_version, category_id, region_id, segment_key,
                 status, train_start_date, train_end_date, training_rows,
                 mae, mape, rmse, smape, r2,
                 hyperparameters, feature_names, feature_importance)
            VALUES
                (:name, :version, :cat, :region, :seg,
                 'trained', :t_start, :t_end, :rows,
                 :mae, :mape, :rmse, :smape, :r2,
                 CAST(:params AS jsonb), CAST(:feats AS jsonb), CAST(:imp AS jsonb))
            ON CONFLICT (model_name, segment_key, model_version) DO UPDATE SET
                mae = EXCLUDED.mae, mape = EXCLUDED.mape,
                rmse = EXCLUDED.rmse, training_rows = EXCLUDED.training_rows,
                hyperparameters = EXCLUDED.hyperparameters,
                feature_importance = EXCLUDED.feature_importance,
                trained_at = now()
            RETURNING model_id
        """),
        {
            "name":    model_name,
            "version": model_version,
            "cat":     category_id,
            "region":  region_id,
            "seg":     seg_key,
            "t_start": str(train_df["ds"].min().date()),
            "t_end":   str(train_df["ds"].max().date()),
            "rows":    len(train_df),
            "mae":     metrics.mae,
            "mape":    metrics.mape,
            "rmse":    metrics.rmse,
            "smape":   metrics.smape,
            "r2":      metrics.r2,
            "params":  json.dumps({k: str(v) for k, v in params.items()}),
            "feats":   json.dumps(feature_names),
            "imp":     json.dumps(feature_imp) if feature_imp else "null",
        },
    ).scalar()
    db.commit()
    return model_id
