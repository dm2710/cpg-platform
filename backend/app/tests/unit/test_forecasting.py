"""
Unit tests for Phase 2 — feature engineering and model utilities.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from app.forecasting.features.engineer import (
    add_calendar_encodings,
    add_lag_features,
    add_rolling_features,
    add_trend_features,
    add_yoy_features,
    segment_key,
)
from app.forecasting.models.base import (
    ForecastOutput,
    ModelMetrics,
    compute_metrics,
    compute_prediction_intervals,
)
from app.forecasting.evaluation.metrics import walk_forward_cv


# ── segment_key ───────────────────────────────────────────

class TestSegmentKey:
    def test_global(self):
        assert segment_key(None, None) == "global"

    def test_with_both(self):
        key = segment_key(1, 2)
        assert "cat=1" in key
        assert "region=2" in key

    def test_with_category_only(self):
        key = segment_key(3, None)
        assert "cat=3" in key
        assert "region=all" in key

    def test_with_region_only(self):
        key = segment_key(None, 5)
        assert "cat=all" in key
        assert "region=5" in key


# ── Lag features ──────────────────────────────────────────

class TestLagFeatures:
    def _make_df(self, n: int = 100) -> pd.DataFrame:
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        return pd.DataFrame({"ds": dates, "y": np.random.rand(n) * 1000 + 500})

    def test_lag_cols_created(self):
        df = add_lag_features(self._make_df())
        for lag in [7, 14, 28, 90, 365]:
            assert f"lag_{lag}d" in df.columns

    def test_lag_7d_correct(self):
        df = self._make_df(50)
        df = add_lag_features(df)
        # Row 10 lag_7d should equal row 3 y
        assert df.iloc[10]["lag_7d"] == pytest.approx(df.iloc[3]["y"])

    def test_first_rows_are_nan(self):
        df = add_lag_features(self._make_df(50))
        assert pd.isna(df.iloc[0]["lag_7d"])
        assert pd.isna(df.iloc[6]["lag_7d"])
        assert not pd.isna(df.iloc[7]["lag_7d"])


# ── Rolling features ──────────────────────────────────────

class TestRollingFeatures:
    def _make_df(self, n: int = 60) -> pd.DataFrame:
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        return pd.DataFrame({"ds": dates, "y": np.arange(n, dtype=float) * 10 + 100})

    def test_rolling_cols_created(self):
        df = add_rolling_features(self._make_df())
        assert "rolling_mean_7d"  in df.columns
        assert "rolling_mean_28d" in df.columns
        assert "rolling_std_7d"   in df.columns
        assert "rolling_min_28d"  in df.columns
        assert "rolling_max_28d"  in df.columns

    def test_rolling_mean_monotone(self):
        """With linearly increasing y, rolling mean should also increase."""
        df = add_rolling_features(self._make_df())
        valid = df["rolling_mean_7d"].dropna()
        diffs = valid.diff().dropna()
        assert (diffs >= 0).all()


# ── YoY features ──────────────────────────────────────────

class TestYoYFeatures:
    def test_yoy_computed_after_365_rows(self):
        n = 400
        dates = pd.date_range("2023-01-01", periods=n, freq="D")
        df = pd.DataFrame({"ds": dates, "y": np.ones(n) * 1000})
        df = add_yoy_features(df)
        # YoY after 365 days should exist
        assert not pd.isna(df.iloc[365]["yoy_revenue"])
        assert pd.isna(df.iloc[364]["yoy_revenue"])

    def test_yoy_growth_zero_for_flat_series(self):
        n = 400
        dates = pd.date_range("2023-01-01", periods=n, freq="D")
        df = pd.DataFrame({"ds": dates, "y": np.ones(n) * 1000})
        df = add_yoy_features(df)
        growth = df["yoy_growth_pct"].dropna()
        assert (growth.abs() < 0.01).all()


# ── Calendar encodings ────────────────────────────────────

class TestCalendarEncodings:
    def test_cyclical_cols_created(self):
        df = pd.DataFrame({
            "day_of_week": [0, 1, 2, 3, 4, 5, 6],
            "month":       [1, 2, 3, 4, 5, 6, 7],
            "week_of_year": [1, 2, 3, 4, 5, 6, 7],
        })
        df = add_calendar_encodings(df)
        for col in ["sin_dow", "cos_dow", "sin_month", "cos_month"]:
            assert col in df.columns

    def test_cyclical_range(self):
        df = pd.DataFrame({
            "day_of_week": list(range(7)),
            "month": list(range(1, 8)),
            "week_of_year": list(range(1, 8)),
        })
        df = add_calendar_encodings(df)
        assert (df["sin_dow"].between(-1, 1)).all()
        assert (df["cos_dow"].between(-1, 1)).all()


# ── compute_metrics ───────────────────────────────────────

class TestComputeMetrics:
    def test_perfect_forecast(self):
        y = np.array([100.0, 200.0, 300.0])
        m = compute_metrics(y, y)
        assert m.mae  == pytest.approx(0.0, abs=1e-6)
        assert m.mape == pytest.approx(0.0, abs=1e-6)
        assert m.r2   == pytest.approx(1.0, abs=1e-4)

    def test_constant_forecast(self):
        y = np.array([100.0, 200.0, 300.0])
        p = np.array([200.0, 200.0, 200.0])
        m = compute_metrics(y, p)
        assert m.mae > 0
        assert m.bias == pytest.approx(0.0, abs=1e-6)  # no systematic bias

    def test_nan_handling(self):
        y = np.array([100.0, np.nan, 300.0])
        p = np.array([100.0, 200.0, 300.0])
        m = compute_metrics(y, p)
        assert m.n == 2

    def test_mape_skips_zero_actuals(self):
        y = np.array([0.0, 100.0, 200.0])
        p = np.array([50.0, 100.0, 200.0])
        m = compute_metrics(y, p)
        assert m.n == 3
        assert m.mape == pytest.approx(0.0, abs=1e-6)  # only 2 non-zero actuals, both perfect


# ── ForecastOutput ────────────────────────────────────────

class TestForecastOutput:
    def test_to_dataframe(self):
        n = 5
        output = ForecastOutput(
            ds=pd.date_range("2024-02-01", periods=n),
            predicted_revenue=np.ones(n) * 1000,
            lower_80=np.ones(n) * 850,
            upper_80=np.ones(n) * 1150,
            lower_95=np.ones(n) * 700,
            upper_95=np.ones(n) * 1300,
        )
        df = output.to_dataframe()
        assert len(df) == n
        assert "predicted_revenue" in df.columns
        assert "lower_80" in df.columns
        assert (df["predicted_revenue"] >= 0).all()

    def test_clips_negative_predictions(self):
        output = ForecastOutput(
            ds=pd.date_range("2024-02-01", periods=3),
            predicted_revenue=np.array([-100.0, 500.0, -50.0]),
            lower_80=np.array([-200.0, 400.0, -150.0]),
            upper_80=np.array([0.0, 600.0, 0.0]),
        )
        df = output.to_dataframe()
        assert (df["predicted_revenue"] >= 0).all()
        assert (df["lower_80"] >= 0).all()


# ── Walk-forward CV ───────────────────────────────────────

class TestWalkForwardCV:
    def _make_model_mock(self):
        """A minimal model mock for CV testing."""
        from app.forecasting.models.base import BaseForecaster, ForecastOutput

        class DummyForecaster(BaseForecaster):
            name = "dummy"

            def fit(self, df):
                self.mean_ = df["y"].mean()
                self.is_fitted = True
                return self

            def predict(self, df):
                n = len(df)
                return ForecastOutput(
                    ds=df["ds"],
                    predicted_revenue=np.full(n, self.mean_),
                )

        return DummyForecaster()

    def _make_df(self, n: int = 200) -> pd.DataFrame:
        dates = pd.date_range("2023-01-01", periods=n, freq="D")
        y = 1000 + 200 * np.sin(np.arange(n) * 2 * np.pi / 365) + np.random.randn(n) * 50
        return pd.DataFrame({"ds": dates, "y": y})

    def test_cv_returns_metrics(self):
        model = self._make_model_mock()
        df    = self._make_df(200)
        result = walk_forward_cv(model, df, horizon_days=14, n_splits=2, min_train_rows=60)
        assert result["cv_folds"] >= 1
        assert "cv_mape" in result
        assert "cv_mae"  in result

    def test_cv_skips_insufficient_data(self):
        model = self._make_model_mock()
        df    = self._make_df(30)
        result = walk_forward_cv(model, df, horizon_days=14, n_splits=3, min_train_rows=60)
        assert result.get("skipped") is True

    def test_cv_n_splits_respected(self):
        model = self._make_model_mock()
        df    = self._make_df(300)
        result = walk_forward_cv(model, df, horizon_days=14, n_splits=3, min_train_rows=60)
        assert result["cv_folds"] == 3
