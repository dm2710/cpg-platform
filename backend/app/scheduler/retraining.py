"""
Automated model retraining scheduler.

Two trigger paths, both ultimately calling the same training pipeline:

  1. Cron schedule -- retrains everything on a fixed cadence
     (default: daily at 03:00), configurable via RETRAINING_CRON.

  2. Drift detection -- runs on a tighter check interval (every hour
     by default) and looks at each deployed model's recent MAPE from
     forecast_accuracy. If MAPE has degraded past
     RETRAINING_DRIFT_MAPE_THRESHOLD, that segment is retrained
     immediately rather than waiting for the next cron run.

Both paths skip a segment if fewer than RETRAINING_MIN_NEW_ROWS rows
have landed in agg_revenue_daily since the model's last training run
-- there's no point retraining on data the model has already seen.

Every decision (ran / skipped, and why) is written to
retraining_schedule_log so retraining behavior is fully auditable,
separate from the per-call training_runs table.

The scheduler is started from the FastAPI lifespan (see main.py) and
is a no-op if RETRAINING_ENABLED=false, which integration tests rely
on to avoid background training during the test suite.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.forecasting.training.trainer import run_training_pipeline

log      = get_logger(__name__)
settings = get_settings()

_scheduler: Optional[BackgroundScheduler] = None

# How often the drift check runs, independent of the cron retrain cadence.
_DRIFT_CHECK_INTERVAL_MINUTES = 60


def start_scheduler() -> None:
    """Start the background scheduler. Idempotent and a no-op if disabled."""
    global _scheduler

    if not settings.retraining_enabled:
        log.info("retraining.disabled")
        return

    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler(timezone="UTC")

    _scheduler.add_job(
        _run_cron_retrain,
        trigger=CronTrigger.from_crontab(settings.retraining_cron),
        id="cron_retrain",
        name="Scheduled full retrain",
        max_instances=1,
        coalesce=True,
    )

    _scheduler.add_job(
        _run_drift_check,
        trigger="interval",
        minutes=_DRIFT_CHECK_INTERVAL_MINUTES,
        id="drift_check",
        name="Model drift check",
        max_instances=1,
        coalesce=True,
    )

    _scheduler.start()
    log.info(
        "retraining.scheduler_started",
        cron=settings.retraining_cron,
        drift_check_interval_min=_DRIFT_CHECK_INTERVAL_MINUTES,
        drift_threshold=settings.retraining_drift_mape_threshold,
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("retraining.scheduler_stopped")


# -- Cron path --------------------------------------------------

def _run_cron_retrain() -> None:
    """Scheduled full retrain across all segments with sufficient new data."""
    t0 = time.monotonic()
    db = SessionLocal()
    try:
        segments_checked, segments_to_retrain = _segments_needing_retrain(db, reason="cron")

        if not segments_to_retrain:
            _log_schedule_decision(
                db, trigger_reason="cron", decision="skipped",
                skip_reason="No segments have enough new data since their last training run",
                segments_checked=segments_checked, segments_drifted=0,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            log.info("retraining.cron_skipped", segments_checked=segments_checked)
            return

        cat_ids = sorted({s[0] for s in segments_to_retrain if s[0] is not None})
        reg_ids = sorted({s[1] for s in segments_to_retrain if s[1] is not None})

        # NOTE: run_training_pipeline treats an empty category_ids/region_ids
        # list the same as None -- "no filter, train everything" -- so if the
        # only eligible segment is the global one (cat_ids and reg_ids both
        # empty), this call retrains every segment rather than just global.
        # That's a safe superset (never incorrect, just sometimes broader
        # than strictly necessary) given the pipeline's existing filter
        # semantics, which we deliberately don't modify here since it's
        # shared code with other callers.
        result = run_training_pipeline(
            db=db,
            category_ids=cat_ids or None,
            region_ids=reg_ids or None,
            triggered_by="scheduler_cron",
        )

        _log_schedule_decision(
            db, trigger_reason="cron", decision="ran",
            training_run_id=result.get("run_id"),
            segments_checked=segments_checked, segments_drifted=0,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
        log.info("retraining.cron_complete", run_id=result.get("run_id"),
                 segments_trained=result.get("segments_trained"))

    except Exception as exc:
        log.error("retraining.cron_failed", error=str(exc))
        _log_schedule_decision(
            db, trigger_reason="cron", decision="skipped",
            skip_reason=f"Unhandled error: {exc}",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
    finally:
        db.close()


# -- Drift path -------------------------------------------------

def _run_drift_check() -> None:
    """Check deployed models' recent accuracy; retrain any that have drifted."""
    t0 = time.monotonic()
    db = SessionLocal()
    try:
        drifted = _find_drifted_segments(db)

        if not drifted:
            _log_schedule_decision(
                db, trigger_reason="drift", decision="skipped",
                skip_reason="No deployed model exceeds the MAPE drift threshold",
                segments_checked=len(drifted), segments_drifted=0,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            return

        # Only retrain drifted segments that also have enough new data --
        # otherwise we'd just retrain on the same data and get the same model.
        _, eligible = _segments_needing_retrain(db, reason="drift", restrict_to=drifted)

        if not eligible:
            _log_schedule_decision(
                db, trigger_reason="drift", decision="skipped",
                skip_reason=f"{len(drifted)} segment(s) drifted but none have enough new data to retrain on",
                segments_checked=len(drifted), segments_drifted=len(drifted),
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            return

        cat_ids = sorted({s[0] for s in eligible if s[0] is not None})
        reg_ids = sorted({s[1] for s in eligible if s[1] is not None})

        # See the matching note in _run_cron_retrain -- empty filter lists
        # mean "train everything" in the underlying pipeline, which is a
        # safe superset if the only drifted+eligible segment is global.
        result = run_training_pipeline(
            db=db,
            category_ids=cat_ids or None,
            region_ids=reg_ids or None,
            triggered_by="scheduler_drift",
        )

        _log_schedule_decision(
            db, trigger_reason="drift", decision="ran",
            training_run_id=result.get("run_id"),
            segments_checked=len(drifted), segments_drifted=len(drifted),
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
        log.warning(
            "retraining.drift_triggered",
            drifted_segments=len(drifted),
            retrained=len(eligible),
            run_id=result.get("run_id"),
        )

    except Exception as exc:
        log.error("retraining.drift_check_failed", error=str(exc))
        _log_schedule_decision(
            db, trigger_reason="drift", decision="skipped",
            skip_reason=f"Unhandled error: {exc}",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
    finally:
        db.close()


# -- Manual trigger (used by the admin API endpoint) -------------

def trigger_manual_retrain(db, category_ids=None, region_ids=None) -> dict:
    t0 = time.monotonic()
    result = run_training_pipeline(
        db=db, category_ids=category_ids, region_ids=region_ids,
        triggered_by="manual_api",
    )
    _log_schedule_decision(
        db, trigger_reason="manual", decision="ran",
        training_run_id=result.get("run_id"),
        duration_ms=int((time.monotonic() - t0) * 1000),
    )
    return result


# -- Helpers ------------------------------------------------------

def _find_drifted_segments(db) -> list[tuple[Optional[int], Optional[int]]]:
    """
    Segments whose deployed model's average MAPE over the last 14 days
    of backfilled accuracy exceeds the configured threshold.
    """
    rows = db.execute(
        text("""
            SELECT mr.category_id, mr.region_id, AVG(fa.mape) AS avg_mape
            FROM model_registry mr
            JOIN forecast_accuracy fa
                ON fa.model_id = mr.model_id AND fa.segment_key = mr.segment_key
            WHERE mr.status = 'deployed'
              AND fa.evaluation_date >= CURRENT_DATE - 14
            GROUP BY mr.category_id, mr.region_id
            HAVING AVG(fa.mape) > :threshold
        """),
        {"threshold": settings.retraining_drift_mape_threshold},
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _segments_needing_retrain(
    db,
    reason: str,
    restrict_to: Optional[list[tuple[Optional[int], Optional[int]]]] = None,
) -> tuple[int, list[tuple[Optional[int], Optional[int]]]]:
    """
    Returns (total_segments_checked, segments_with_enough_new_data).
    A segment qualifies if rows have landed in agg_revenue_daily with
    refreshed_at after that segment's most recent deployed model was
    trained, and there are at least retraining_min_new_rows of them.
    """
    rows = db.execute(text("""
        SELECT DISTINCT category_id, region_id FROM agg_revenue_daily
    """)).fetchall()
    # Always include the global segment as a candidate, matching
    # app.forecasting.features.engineer.get_segments() -- the global
    # model trains on pooled data across everything and is never
    # itself a literal (category_id, region_id) row in agg_revenue_daily.
    all_segments = [(None, None)] + [(r[0], r[1]) for r in rows]

    if restrict_to is not None:
        restrict_set = set(restrict_to)
        all_segments = [s for s in all_segments if s in restrict_set]

    eligible = []
    for cat_id, reg_id in all_segments:
        last_trained = db.execute(
            text("""
                SELECT MAX(trained_at) FROM model_registry
                WHERE status = 'deployed'
                  AND category_id IS NOT DISTINCT FROM :cat
                  AND region_id   IS NOT DISTINCT FROM :reg
            """),
            {"cat": cat_id, "reg": reg_id},
        ).scalar()

        # No model trained yet for this segment -- always eligible.
        if last_trained is None:
            eligible.append((cat_id, reg_id))
            continue

        new_rows = db.execute(
            text("""
                SELECT COUNT(*) FROM agg_revenue_daily
                WHERE category_id IS NOT DISTINCT FROM :cat
                  AND region_id   IS NOT DISTINCT FROM :reg
                  AND refreshed_at > :since
            """),
            {"cat": cat_id, "reg": reg_id, "since": last_trained},
        ).scalar()

        if new_rows >= settings.retraining_min_new_rows:
            eligible.append((cat_id, reg_id))

    return len(all_segments), eligible


def _log_schedule_decision(
    db,
    trigger_reason: str,
    decision: str,
    skip_reason: Optional[str] = None,
    training_run_id: Optional[int] = None,
    segments_checked: Optional[int] = None,
    segments_drifted: Optional[int] = None,
    duration_ms: Optional[int] = None,
) -> None:
    try:
        db.execute(
            text("""
                INSERT INTO retraining_schedule_log
                    (trigger_reason, decision, skip_reason, training_run_id,
                     segments_checked, segments_drifted, duration_ms)
                VALUES (:reason, :decision, :skip, :run_id, :checked, :drifted, :duration)
            """),
            {
                "reason": trigger_reason, "decision": decision, "skip": skip_reason,
                "run_id": training_run_id, "checked": segments_checked,
                "drifted": segments_drifted, "duration": duration_ms,
            },
        )
        db.commit()
    except Exception as exc:
        log.warning("retraining.log_decision_failed", error=str(exc))
        db.rollback()
