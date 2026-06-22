"""
LightGBM forecasting model.

Uses gradient boosting on the full feature matrix (lags, rolling stats,
calendar encodings, signal regressors) to learn demand patterns.

Key design choices:
  - Three models trained: point (regression), lower quantile (0.10, 0.025),
    upper quantile (0.90, 0.975) → 80% and 95% prediction intervals
  - Optuna hyperparameter search (fast, 30-trial budget by default)
  - TimeSeriesSplit cross-validation to avoid data leakage
  - Feature importance stored for model registry
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from app.core.logging import get_logger
from app.forecasting.models.base import (
    BaseForecaster,
    ForecastOutput,
    ModelMetrics,
    compute_metrics,
    compute_prediction_intervals,
)

log = get_logger(__name__)

# Features passed to LightGBM (excludes raw date, target)
LGBM_FEATURE_COLS = [
    # Calendar (cyclical encodings)
    "sin_dow", "cos_dow", "sin_month", "cos_month", "sin_week", "cos_week",
    "day_of_month", "week_of_year", "month", "quarter", "year",
    "is_weekend", "is_month_start", "is_month_end",
    "is_quarter_start", "is_quarter_end", "is_public_holiday",
    "retail_season_ord",
    # Lag features
    "lag_7d", "lag_14d", "lag_28d", "lag_90d", "lag_365d",
    # Rolling stats
    "rolling_mean_7d", "rolling_mean_14d", "rolling_mean_28d",
    "rolling_std_7d", "rolling_std_28d",
    "rolling_min_28d", "rolling_max_28d",
    # YoY / trend
    "yoy_revenue", "yoy_growth_pct",
    "revenue_trend_7d", "revenue_trend_28d",
    # Signals
    "active_promo_count", "max_discount_pct",
    "daily_campaign_spend", "competitor_price_index",
    # Weather
    "avg_temp_c", "precipitation_mm", "is_extreme_weather",
    # Demographics
    "median_income_usd", "urban_pct",
]

DEFAULT_PARAMS = {
    "n_estimators":    500,
    "learning_rate":   0.05,
    "max_depth":       6,
    "num_leaves":      31,
    "min_child_samples": 20,
    "subsample":       0.8,
    "colsample_bytree": 0.8,
    "reg_alpha":       0.1,
    "reg_lambda":      0.1,
    "random_state":    42,
    "n_jobs":          -1,
    "verbose":         -1,
}


class LightGBMForecaster(BaseForecaster):
    name = "lightgbm"

    def __init__(self, params: Optional[dict] = None, tune: bool = False, n_trials: int = 30):
        super().__init__({**DEFAULT_PARAMS, **(params or {})})
        self._tune     = tune
        self._n_trials = n_trials
        self._model_point  = None
        self._model_q10    = None   # 80% lower
        self._model_q90    = None   # 80% upper
        self._model_q025   = None   # 95% lower
        self._model_q975   = None   # 95% upper
        self._feature_cols: list[str] = []
        self._residual_std: float = 0.0

    def _get_feature_cols(self, df: pd.DataFrame) -> list[str]:
        return [c for c in LGBM_FEATURE_COLS if c in df.columns]

    def _prepare(self, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
        feats = self._get_feature_cols(df)
        X = df[feats].fillna(0).astype(float)
        y = df["y"].astype(float) if "y" in df.columns else pd.Series(np.nan, index=df.index)
        return X, y

    def _tune_params(self, X: pd.DataFrame, y: pd.Series) -> dict:
        """Optuna hyperparameter search with TimeSeriesSplit CV."""
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        import lightgbm as lgb
        tscv = TimeSeriesSplit(n_splits=3)

        def objective(trial):
            p = {
                "n_estimators":      trial.suggest_int("n_estimators", 100, 1000),
                "learning_rate":     trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
                "max_depth":         trial.suggest_int("max_depth", 3, 10),
                "num_leaves":        trial.suggest_int("num_leaves", 15, 127),
                "min_child_samples": trial.suggest_int("min_child_samples", 5, 50),
                "subsample":         trial.suggest_float("subsample", 0.5, 1.0),
                "colsample_bytree":  trial.suggest_float("colsample_bytree", 0.5, 1.0),
                "reg_alpha":         trial.suggest_float("reg_alpha", 1e-4, 10.0, log=True),
                "reg_lambda":        trial.suggest_float("reg_lambda", 1e-4, 10.0, log=True),
                "random_state":      42,
                "n_jobs":            -1,
                "verbose":           -1,
            }
            maes = []
            for train_idx, val_idx in tscv.split(X):
                m = lgb.LGBMRegressor(**p)
                m.fit(X.iloc[train_idx], y.iloc[train_idx],
                      eval_set=[(X.iloc[val_idx], y.iloc[val_idx])],
                      callbacks=[lgb.early_stopping(50, verbose=False),
                                 lgb.log_evaluation(-1)])
                preds = m.predict(X.iloc[val_idx])
                maes.append(np.mean(np.abs(y.iloc[val_idx].values - preds)))
            return float(np.mean(maes))

        study = optuna.create_study(direction="minimize")
        study.optimize(objective, n_trials=self._n_trials, show_progress_bar=False)

        log.info("lgbm.tune_complete", best_mae=study.best_value, params=study.best_params)
        return {**DEFAULT_PARAMS, **study.best_params}

    def fit(self, df: pd.DataFrame) -> "LightGBMForecaster":
        import lightgbm as lgb

        self._validate_df(df)
        train = df[df["y"].notna()].copy()

        if len(train) < 30:
            raise ValueError(f"LightGBM requires ≥30 training rows, got {len(train)}")

        X_train, y_train = self._prepare(train)
        self._feature_cols = list(X_train.columns)

        if self._tune:
            log.info("lgbm.tuning", n_trials=self._n_trials, rows=len(train))
            self.params = self._tune_params(X_train, y_train)

        # Point forecast model
        self._model_point = lgb.LGBMRegressor(**self.params)
        self._model_point.fit(X_train, y_train)

        # Quantile models for confidence intervals
        q_params = {**self.params, "n_estimators": min(self.params["n_estimators"], 300)}
        for q, attr in [(0.10, "_model_q10"), (0.90, "_model_q90"),
                        (0.025, "_model_q025"), (0.975, "_model_q975")]:
            model = lgb.LGBMRegressor(objective="quantile", alpha=q, **q_params)
            model.fit(X_train, y_train)
            setattr(self, attr, model)

        # Residual std for fallback CI
        in_sample = self._model_point.predict(X_train)
        self._residual_std = float(np.std(y_train.values - in_sample))

        # Feature importance
        self._feature_importance = dict(
            zip(self._feature_cols, self._model_point.feature_importances_.tolist())
        )

        self.is_fitted      = True
        self.feature_names_ = self._feature_cols
        self.train_start_   = train["ds"].min().date() if hasattr(train["ds"].iloc[0], "date") else None
        self.train_end_     = train["ds"].max().date() if hasattr(train["ds"].iloc[0], "date") else None
        self.training_rows_ = len(train)

        log.info(
            "lgbm.fitted",
            rows=self.training_rows_,
            features=len(self._feature_cols),
            top_features=sorted(
                self._feature_importance.items(), key=lambda x: -x[1]
            )[:5],
        )
        return self

    def predict(self, df: pd.DataFrame) -> ForecastOutput:
        if not self.is_fitted:
            raise RuntimeError("Model must be fitted before calling predict()")

        pred_df = df.copy()
        X, _ = self._prepare(pred_df)

        yhat     = np.maximum(self._model_point.predict(X), 0)
        lower_80 = np.maximum(self._model_q10.predict(X), 0)
        upper_80 = np.maximum(self._model_q90.predict(X), 0)
        lower_95 = np.maximum(self._model_q025.predict(X), 0)
        upper_95 = np.maximum(self._model_q975.predict(X), 0)

        return ForecastOutput(
            ds=pred_df["ds"],
            predicted_revenue=yhat,
            lower_80=lower_80,
            upper_80=upper_80,
            lower_95=lower_95,
            upper_95=upper_95,
        )

    def get_params(self) -> dict:
        params = dict(self.params)
        if hasattr(self, "_feature_importance"):
            params["feature_importance"] = dict(
                sorted(self._feature_importance.items(), key=lambda x: -x[1])[:20]
            )
        return params
