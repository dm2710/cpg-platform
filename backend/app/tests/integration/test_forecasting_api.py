"""
Integration tests for Phase 2 forecasting API.

These tests seed the DB with synthetic data, then exercise the full
train -> predict -> retrieve -> backfill flow.

Note on field naming: request bodies use snake_case (accepted via
populate_by_name=True on CamelBase), but all response bodies use
camelCase, since every schema in this API derives from CamelBase
with alias_generator set. Response field reads in this file must
use camelCase accordingly.
"""

from datetime import date, timedelta

import numpy as np
import pytest

from app.tests.conftest import seed_category, seed_region


def _seed_revenue(db, n_days: int = 120) -> tuple[int, int]:
    """Seed agg_revenue_daily with n_days of synthetic data."""
    from sqlalchemy import text

    cat_id = seed_category(db, "Electronics")
    reg_id = seed_region(db, "North America")

    today = date.today()
    for i in range(n_days, 0, -1):
        d  = today - timedelta(days=i)
        rev = 1000 + 200 * np.sin(i * 2 * np.pi / 365) + np.random.randn() * 50

        db.execute(
            text("""
                INSERT INTO agg_revenue_daily (agg_date, category_id, region_id, total_revenue, total_quantity, txn_count)
                VALUES (:date, :cat, :region, :rev, :qty, :cnt)
                ON CONFLICT DO NOTHING
            """),
            {"date": d, "cat": cat_id, "region": reg_id, "rev": max(rev, 10), "qty": 50, "cnt": 10},
        )
    db.commit()
    return cat_id, reg_id


# -- Health ---------------------------------------------------

class TestForecastingHealth:
    def test_health_ok(self, client):
        r = client.get("/api/v1/health")
        assert r.status_code == 200


# -- Feature store ----------------------------------------------

class TestFeatureStore:
    def test_feature_query_empty(self, client):
        r = client.get("/api/v1/forecasting/features")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_feature_query_with_data(self, client, db):
        cat_id, reg_id = _seed_revenue(db, n_days=90)
        r = client.get(f"/api/v1/forecasting/features?category_id={cat_id}&region_id={reg_id}&limit=10")
        assert r.status_code == 200
        # Feature store may be empty until a training run populates it
        assert isinstance(r.json(), list)


# -- Training runs ------------------------------------------------

class TestTrainingRuns:
    def test_list_runs_empty(self, client):
        r = client.get("/api/v1/forecasting/runs")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_trigger_training_no_data(self, client):
        """Training with no data should complete with all segments skipped."""
        r = client.post(
            "/api/v1/forecasting/train?run_sync=true",
            json={"model_names": ["lightgbm"], "horizon_days": 30},
        )
        assert r.status_code == 200
        data = r.json()
        assert "segmentsSkipped" in data
        # No data -> all skipped, none failed
        assert data.get("segmentsFailed", 0) == 0

    def test_trigger_training_with_data(self, client, db):
        """With sufficient data, at least one segment should train."""
        cat_id, reg_id = _seed_revenue(db, n_days=120)

        r = client.post(
            "/api/v1/forecasting/train?run_sync=true",
            json={
                "model_names":  ["lightgbm"],
                "horizon_days": 14,
                "category_ids": [cat_id],
                "region_ids":   [reg_id],
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["segmentsTrained"] >= 1

    def test_run_detail(self, client, db):
        _seed_revenue(db, n_days=90)
        train_r = client.post(
            "/api/v1/forecasting/train?run_sync=true",
            json={"model_names": ["lightgbm"], "horizon_days": 14},
        )
        run_id = train_r.json().get("runId")
        if not run_id:
            pytest.skip("No run created")

        r = client.get(f"/api/v1/forecasting/runs/{run_id}")
        assert r.status_code == 200
        assert r.json()["runId"] == run_id

    def test_run_not_found(self, client):
        r = client.get("/api/v1/forecasting/runs/999999")
        assert r.status_code == 404


# -- Model registry -----------------------------------------------

class TestModelRegistry:
    def test_list_models_empty(self, client):
        r = client.get("/api/v1/forecasting/models")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_model_registered_after_training(self, client, db):
        cat_id, reg_id = _seed_revenue(db, n_days=120)
        client.post(
            "/api/v1/forecasting/train?run_sync=true",
            json={
                "model_names":  ["lightgbm"],
                "horizon_days": 14,
                "category_ids": [cat_id],
                "region_ids":   [reg_id],
            },
        )
        r = client.get("/api/v1/forecasting/models")
        assert r.status_code == 200
        # At least one model should be registered (global + segment)
        models = r.json()
        assert len(models) >= 1
        assert all("modelId" in m for m in models)

    def test_model_detail(self, client, db):
        cat_id, reg_id = _seed_revenue(db, n_days=120)
        client.post(
            "/api/v1/forecasting/train?run_sync=true",
            json={"model_names": ["lightgbm"], "horizon_days": 14,
                  "category_ids": [cat_id], "region_ids": [reg_id]},
        )
        models = client.get("/api/v1/forecasting/models").json()
        if not models:
            pytest.skip("No models registered")

        model_id = models[0]["modelId"]
        r = client.get(f"/api/v1/forecasting/models/{model_id}")
        assert r.status_code == 200
        assert r.json()["modelId"] == model_id

    def test_model_not_found(self, client):
        r = client.get("/api/v1/forecasting/models/999999")
        assert r.status_code == 404


# -- Prediction ------------------------------------------------

class TestPrediction:
    def _train_and_seed(self, client, db, n_days=120):
        cat_id, reg_id = _seed_revenue(db, n_days=n_days)
        client.post(
            "/api/v1/forecasting/train?run_sync=true",
            json={"model_names": ["lightgbm"], "horizon_days": 14,
                  "category_ids": [cat_id], "region_ids": [reg_id]},
        )
        return cat_id, reg_id

    def test_predict_no_model_returns_404(self, client):
        r = client.post("/api/v1/forecasting/predict", json={
            "category_id": 999, "region_id": 999, "horizon_days": 7,
        })
        assert r.status_code in (404, 422)

    def test_predict_segment_with_model(self, client, db):
        cat_id, reg_id = self._train_and_seed(client, db)

        r = client.post("/api/v1/forecasting/predict", json={
            "category_id":  cat_id,
            "region_id":    reg_id,
            "horizon_days": 7,
        })
        assert r.status_code == 200
        data = r.json()
        assert "forecasts" in data
        assert len(data["forecasts"]) > 0
        # All predictions should be non-negative
        for point in data["forecasts"]:
            assert float(point["predictedRevenue"]) >= 0

    def test_forecast_has_confidence_intervals(self, client, db):
        cat_id, reg_id = self._train_and_seed(client, db)

        r = client.post("/api/v1/forecasting/predict", json={
            "category_id": cat_id, "region_id": reg_id, "horizon_days": 7,
        })
        assert r.status_code == 200
        forecasts = r.json()["forecasts"]
        if forecasts:
            f = forecasts[0]
            # CI bands should exist (may be None for some models but present as keys)
            assert "lower80" in f
            assert "upper80" in f

    def test_retrieve_stored_forecasts(self, client, db):
        cat_id, reg_id = self._train_and_seed(client, db)
        client.post("/api/v1/forecasting/predict", json={
            "category_id": cat_id, "region_id": reg_id, "horizon_days": 14,
        })

        r = client.get(f"/api/v1/forecasting/forecasts?category_id={cat_id}&region_id={reg_id}")
        assert r.status_code == 200
        assert len(r.json()) >= 1


# -- Batch prediction -------------------------------------------

class TestBatchPrediction:
    def test_batch_predict_sync(self, client, db):
        _seed_revenue(db, n_days=120)
        client.post(
            "/api/v1/forecasting/train?run_sync=true",
            json={"model_names": ["lightgbm"], "horizon_days": 14},
        )

        r = client.post(
            "/api/v1/forecasting/predict/batch?run_sync=true",
            json={"horizon_days": 7},
        )
        assert r.status_code == 200
        data = r.json()
        assert "segmentsTotal"    in data
        assert "segmentsForecast" in data


# -- Accuracy and backfill ---------------------------------------

class TestAccuracy:
    def test_accuracy_empty(self, client):
        r = client.get("/api/v1/forecasting/accuracy?model_id=1&segment_key=global")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_model_comparison(self, client, db):
        _seed_revenue(db, n_days=120)
        client.post(
            "/api/v1/forecasting/train?run_sync=true",
            json={"model_names": ["lightgbm"], "horizon_days": 14},
        )
        r = client.get("/api/v1/forecasting/models/compare?segment_key=global")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
