"""
Model evaluation — walk-forward backtesting and accuracy tracking.

walk_forward_cv():
  Simulates production forecasting by repeatedly training on expanding
  windows and evaluating on the next horizon_days window.
  Catches issues (overfitting, poor generalisation) that hold-out alone misses.

backfill_actuals():
  Once real revenue data arrives for a past forecast date, fills in
  forecast_results.actual_revenue and recomputes error_pct.
  Also writes a row to forecast_accuracy for trend tracking.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.forecasting.models.base import BaseForecaster, ModelMetrics, compute_metrics

log = get_logger(__name__)


def walk_forward_cv(
    model: BaseForecaster,
    df: pd.DataFrame,
    horizon_days: int = 30,
    n_splits: int = 3,
    min_train_rows: int = 60,
) -> dict:
    """
    Walk-forward (expanding window) cross-validation.

    Returns a dict with per-fold metrics and aggregate statistics.
    """
    df = df[df["y"].notna()].copy().sort_values("ds").reset_index(drop=True)

    if len(df) < min_train_rows + horizon_days:
        return {
            "cv_folds": 0,
            "skipped": True,
            "reason": f"Not enough data ({len(df)} rows, need {min_train_rows + horizon_days})",
        }

    total_rows = len(df)
    # Step size between folds
    step = max(horizon_days, (total_rows - min_train_rows - horizon_days) // n_splits)

    folds: list[dict] = []
    fold_idx = 0

    for split_point in range(
        min_train_rows,
        total_rows - horizon_days + 1,
        step,
    ):
        if fold_idx >= n_splits:
            break

        train_df = df.iloc[:split_point].copy()
        val_df   = df.iloc[split_point: split_point + horizon_days].copy()

        if len(val_df) == 0:
            continue

        try:
            import copy
            fold_model = copy.deepcopy(model)
            fold_model.fit(train_df)

            output   = fold_model.predict(val_df)
            actuals  = val_df["y"].values
            metrics  = compute_metrics(actuals, output.predicted_revenue)

            folds.append({
                "fold":          fold_idx + 1,
                "train_rows":    len(train_df),
                "val_rows":      len(val_df),
                "train_end":     str(train_df["ds"].max().date()),
                "val_start":     str(val_df["ds"].min().date()),
                "val_end":       str(val_df["ds"].max().date()),
                **metrics.to_dict(),
            })
            fold_idx += 1

            log.info(
                "cv.fold_complete",
                fold=fold_idx,
                mae=metrics.mae,
                mape=metrics.mape,
            )

        except Exception as exc:
            log.warning("cv.fold_failed", fold=fold_idx + 1, error=str(exc))
            folds.append({"fold": fold_idx + 1, "error": str(exc)})
            fold_idx += 1

    if not folds or all("error" in f for f in folds):
        return {"cv_folds": len(folds), "error": "All folds failed", "folds": folds}

    good_folds = [f for f in folds if "error" not in f]
    agg = {
        "cv_folds":  len(folds),
        "folds":     folds,
        "cv_mae":    float(np.mean([f["mae"]  for f in good_folds])),
        "cv_mape":   float(np.mean([f["mape"] for f in good_folds])),
        "cv_rmse":   float(np.mean([f["rmse"] for f in good_folds])),
        "cv_r2":     float(np.mean([f["r2"]   for f in good_folds])),
        "cv_mae_std":  float(np.std([f["mae"]  for f in good_folds])),
        "cv_mape_std": float(np.std([f["mape"] for f in good_folds])),
    }

    log.info(
        "cv.complete",
        cv_mape=agg["cv_mape"],
        cv_mae=agg["cv_mae"],
        folds=agg["cv_folds"],
    )
    return agg


def evaluate_on_holdout(
    model: BaseForecaster,
    train_df: pd.DataFrame,
    holdout_df: pd.DataFrame,
) -> ModelMetrics:
    """Train on train_df, evaluate on holdout_df."""
    model.fit(train_df)
    output  = model.predict(holdout_df)
    actuals = holdout_df[holdout_df["y"].notna()]["y"].values
    return compute_metrics(actuals, output.predicted_revenue[:len(actuals)])


def backfill_actuals(
    db: Session,
    model_id: int,
    segment_key: str,
    as_of_date: Optional[date] = None,
) -> dict:
    """
    Backfill actual_revenue on forecast_results rows where actuals have
    now arrived (i.e. forecast_date <= today and actual not yet filled).
    Also writes rows to forecast_accuracy.
    """
    cutoff = as_of_date or date.today()

    # Find forecast rows without actuals, within the settled window
    rows = db.execute(
        text("""
            SELECT fr.forecast_id, fr.forecast_date,
                   fr.predicted_revenue, fr.segment_key
            FROM forecast_results fr
            WHERE fr.model_id    = :model_id
              AND fr.segment_key = :seg_key
              AND fr.forecast_date <= :cutoff
              AND fr.actual_revenue IS NULL
        """),
        {"model_id": model_id, "seg_key": segment_key, "cutoff": cutoff},
    ).mappings().all()

    if not rows:
        return {"backfilled": 0}

    # Pull actuals from agg_revenue_daily
    # Extract category_id and region_id from segment_key
    cat_id, reg_id = _parse_segment_key(segment_key)
    actuals_df = _load_actuals(db, cat_id, reg_id, cutoff)

    updated = 0
    for row in rows:
        actual = actuals_df.get(row["forecast_date"])
        if actual is None:
            continue

        error_pct = (
            abs(float(row["predicted_revenue"]) - actual) / actual * 100
            if actual != 0 else None
        )

        db.execute(
            text("""
                UPDATE forecast_results
                SET actual_revenue = :actual, error_pct = :err_pct
                WHERE forecast_id = :fid
            """),
            {"actual": actual, "err_pct": error_pct, "fid": row["forecast_id"]},
        )

        # Write accuracy row
        train_date = db.execute(
            text("SELECT trained_at::date FROM model_registry WHERE model_id = :mid"),
            {"mid": model_id},
        ).scalar()

        horizon = (row["forecast_date"] - (train_date or row["forecast_date"])).days

        db.execute(
            text("""
                INSERT INTO forecast_accuracy
                    (model_id, segment_key, evaluation_date, horizon_days,
                     mae, mape, bias)
                VALUES (:mid, :seg, :eval_date, :horizon, :mae, :mape, :bias)
                ON CONFLICT (model_id, segment_key, evaluation_date, horizon_days)
                DO UPDATE SET mae=EXCLUDED.mae, mape=EXCLUDED.mape,
                              bias=EXCLUDED.bias, computed_at=now()
            """),
            {
                "mid":       model_id,
                "seg":       segment_key,
                "eval_date": row["forecast_date"],
                "horizon":   max(0, horizon),
                "mae":       abs(float(row["predicted_revenue"]) - actual),
                "mape":      error_pct,
                "bias":      float(row["predicted_revenue"]) - actual,
            },
        )
        updated += 1

    db.commit()
    log.info("backfill.complete", model_id=model_id, segment_key=segment_key, updated=updated)
    return {"backfilled": updated}


def compare_models(
    db: Session,
    segment_key: str,
    horizon_days: int = 30,
) -> list[dict]:
    """
    Compare deployed models for a segment by their recent accuracy.
    Returns ranked list (best first by MAPE).
    """
    rows = db.execute(
        text("""
            SELECT mr.model_id, mr.model_name, mr.model_version, mr.mape, mr.mae, mr.rmse,
                   AVG(fa.mape)  AS recent_mape,
                   AVG(fa.mae)   AS recent_mae,
                   COUNT(fa.id)  AS eval_points
            FROM model_registry mr
            LEFT JOIN forecast_accuracy fa
                ON fa.model_id = mr.model_id
               AND fa.segment_key = mr.segment_key
               AND fa.horizon_days <= :horizon
               AND fa.evaluation_date >= CURRENT_DATE - INTERVAL '90 days'
            WHERE mr.segment_key = :seg
              AND mr.status IN ('trained','deployed')
            GROUP BY mr.model_id, mr.model_name, mr.model_version, mr.mape, mr.mae, mr.rmse
            ORDER BY COALESCE(AVG(fa.mape), mr.mape) ASC NULLS LAST
        """),
        {"seg": segment_key, "horizon": horizon_days},
    ).mappings().all()

    return [dict(r) for r in rows]


def _parse_segment_key(seg_key: str) -> tuple[Optional[int], Optional[int]]:
    if seg_key == "global":
        return None, None
    cat_id = reg_id = None
    for part in seg_key.split("|"):
        if part.startswith("cat=") and part[4:] != "all":
            try: cat_id = int(part[4:])
            except ValueError: pass
        if part.startswith("region=") and part[7:] != "all":
            try: reg_id = int(part[7:])
            except ValueError: pass
    return cat_id, reg_id


def _load_actuals(
    db: Session,
    category_id: Optional[int],
    region_id: Optional[int],
    up_to: date,
) -> dict[date, float]:
    filters = ["agg_date <= :cutoff"]
    params: dict = {"cutoff": up_to}
    if category_id is not None:
        filters.append("category_id = :cat")
        params["cat"] = category_id
    if region_id is not None:
        filters.append("region_id = :region")
        params["region"] = region_id

    where = " AND ".join(filters)
    rows = db.execute(
        text(f"""
            SELECT agg_date, SUM(total_revenue) AS total_revenue
            FROM agg_revenue_daily
            WHERE {where}
            GROUP BY agg_date
        """),
        params,
    ).fetchall()
    return {r[0]: float(r[1]) for r in rows}
