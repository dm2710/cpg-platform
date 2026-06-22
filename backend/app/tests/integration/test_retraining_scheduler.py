"""
Tests for app.scheduler.retraining.

These need a real database (segment discovery and drift detection are
SQL-driven), so they live alongside the other DB-backed tests and use
the shared `db` fixture rather than being pure unit tests.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.scheduler.retraining import (
    _find_drifted_segments,
    _log_schedule_decision,
    _segments_needing_retrain,
)
from app.tests.conftest import seed_category, seed_region


def _seed_revenue_rows(db, category_id: int, region_id: int, n_days: int = 10):
    today = date.today()
    for i in range(n_days):
        d = today - timedelta(days=i)
        db.execute(
            text("""
                INSERT INTO agg_revenue_daily (agg_date, category_id, region_id, total_revenue, total_quantity, txn_count)
                VALUES (:d, :cat, :reg, 1000, 50, 10)
                ON CONFLICT (agg_date, category_id, region_id) DO NOTHING
            """),
            {"d": d, "cat": category_id, "reg": region_id},
        )
    db.commit()


def _seed_deployed_model(db, category_id, region_id, segment_key: str, mape: float = 10.0):
    db.execute(
        text("""
            INSERT INTO model_registry
                (model_name, model_version, category_id, region_id, segment_key, status, mape)
            VALUES ('lightgbm', 'test-v1', :cat, :reg, :seg, 'deployed', :mape)
        """),
        {"cat": category_id, "reg": region_id, "seg": segment_key, "mape": mape},
    )
    db.commit()
    return db.execute(
        text("SELECT model_id FROM model_registry WHERE segment_key = :seg AND status='deployed' ORDER BY model_id DESC LIMIT 1"),
        {"seg": segment_key},
    ).scalar()


class TestSegmentsNeedingRetrain:
    def test_segment_with_no_model_is_eligible(self, db):
        cat_id = seed_category(db, "NeverTrained")
        reg_id = seed_region(db, "NeverTrainedRegion")
        _seed_revenue_rows(db, cat_id, reg_id, n_days=5)

        checked, eligible = _segments_needing_retrain(db, reason="test")
        assert (cat_id, reg_id) in eligible

    def test_global_segment_always_a_candidate(self, db):
        cat_id = seed_category(db, "AnyCategory")
        reg_id = seed_region(db, "AnyRegion")
        _seed_revenue_rows(db, cat_id, reg_id, n_days=5)

        checked, eligible = _segments_needing_retrain(db, reason="test")
        assert (None, None) in eligible

    def test_segment_with_recent_model_and_no_new_data_not_eligible(self, db):
        cat_id = seed_category(db, "FreshlyTrained")
        reg_id = seed_region(db, "FreshlyTrainedRegion")
        _seed_revenue_rows(db, cat_id, reg_id, n_days=5)

        seg_key = f"cat={cat_id}|region={reg_id}"
        _seed_deployed_model(db, cat_id, reg_id, seg_key)

        # No new rows have landed since the model was trained (it was
        # just trained, all rows predate it).
        checked, eligible = _segments_needing_retrain(db, reason="test")
        assert (cat_id, reg_id) not in eligible

    def test_restrict_to_filters_candidates(self, db):
        cat_a = seed_category(db, "RestrictA")
        cat_b = seed_category(db, "RestrictB")
        reg_id = seed_region(db, "RestrictRegion")
        _seed_revenue_rows(db, cat_a, reg_id, n_days=3)
        _seed_revenue_rows(db, cat_b, reg_id, n_days=3)

        checked, eligible = _segments_needing_retrain(
            db, reason="test", restrict_to=[(cat_a, reg_id)]
        )
        assert (cat_a, reg_id) in eligible
        assert (cat_b, reg_id) not in eligible


class TestDriftDetection:
    def test_no_drift_when_mape_is_low(self, db):
        cat_id = seed_category(db, "LowMape")
        reg_id = seed_region(db, "LowMapeRegion")
        seg_key = f"cat={cat_id}|region={reg_id}"
        model_id = _seed_deployed_model(db, cat_id, reg_id, seg_key, mape=5.0)

        db.execute(
            text("""
                INSERT INTO forecast_accuracy (model_id, segment_key, evaluation_date, horizon_days, mape)
                VALUES (:mid, :seg, CURRENT_DATE - 1, 7, 5.0)
            """),
            {"mid": model_id, "seg": seg_key},
        )
        db.commit()

        drifted = _find_drifted_segments(db)
        assert (cat_id, reg_id) not in drifted

    def test_drift_detected_when_mape_exceeds_threshold(self, db):
        cat_id = seed_category(db, "HighMape")
        reg_id = seed_region(db, "HighMapeRegion")
        seg_key = f"cat={cat_id}|region={reg_id}"
        model_id = _seed_deployed_model(db, cat_id, reg_id, seg_key, mape=45.0)

        db.execute(
            text("""
                INSERT INTO forecast_accuracy (model_id, segment_key, evaluation_date, horizon_days, mape)
                VALUES (:mid, :seg, CURRENT_DATE - 1, 7, 45.0)
            """),
            {"mid": model_id, "seg": seg_key},
        )
        db.commit()

        drifted = _find_drifted_segments(db)
        assert (cat_id, reg_id) in drifted

    def test_old_accuracy_data_ignored(self, db):
        """Accuracy rows older than 14 days shouldn't count toward drift."""
        cat_id = seed_category(db, "StaleAccuracy")
        reg_id = seed_region(db, "StaleAccuracyRegion")
        seg_key = f"cat={cat_id}|region={reg_id}"
        model_id = _seed_deployed_model(db, cat_id, reg_id, seg_key, mape=50.0)

        db.execute(
            text("""
                INSERT INTO forecast_accuracy (model_id, segment_key, evaluation_date, horizon_days, mape)
                VALUES (:mid, :seg, CURRENT_DATE - 30, 7, 50.0)
            """),
            {"mid": model_id, "seg": seg_key},
        )
        db.commit()

        drifted = _find_drifted_segments(db)
        assert (cat_id, reg_id) not in drifted


class TestScheduleLogging:
    def test_log_ran_decision(self, db):
        _log_schedule_decision(db, trigger_reason="cron", decision="ran", duration_ms=100)
        row = db.execute(
            text("SELECT trigger_reason, decision FROM retraining_schedule_log ORDER BY schedule_log_id DESC LIMIT 1")
        ).first()
        assert row[0] == "cron"
        assert row[1] == "ran"

    def test_log_skipped_decision_with_reason(self, db):
        _log_schedule_decision(
            db, trigger_reason="drift", decision="skipped",
            skip_reason="No segments drifted", duration_ms=50,
        )
        row = db.execute(
            text("SELECT decision, skip_reason FROM retraining_schedule_log ORDER BY schedule_log_id DESC LIMIT 1")
        ).first()
        assert row[0] == "skipped"
        assert "No segments drifted" in row[1]
