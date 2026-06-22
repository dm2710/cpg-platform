"""
Prophet forecasting model.

Wraps Meta's Prophet with CPG-specific configuration:
  - Multiplicative seasonality (revenue responds proportionally)
  - US + global public holidays from dim_calendar
  - Optional external regressors: promo, campaign spend, competitor index
  - Native 80% and 95% confidence intervals
  - Component decomposition (trend, weekly, yearly, holiday)
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from app.core.logging import get_logger
from app.forecasting.models.base import (
    BaseForecaster,
    ForecastOutput,
    ModelMetrics,
    compute_metrics,
)

log = get_logger(__name__)

# Regressors passed from feature matrix to Prophet
PROPHET_REGRESSORS = [
    "active_promo_count",
    "max_discount_pct",
    "daily_campaign_spend",
    "competitor_price_index",
    "is_public_holiday",
]

DEFAULT_PARAMS = {
    "seasonality_mode":         "multiplicative",
    "yearly_seasonality":       True,
    "weekly_seasonality":       True,
    "daily_seasonality":        False,
    "changepoint_prior_scale":  0.05,
    "seasonality_prior_scale":  10.0,
    "holidays_prior_scale":     10.0,
    "interval_width":           0.80,
    "mcmc_samples":             0,       # 0 = MAP (fast); >0 = full Bayesian
}


class ProphetForecaster(BaseForecaster):
    name = "prophet"

    def __init__(self, params: Optional[dict] = None):
        super().__init__({**DEFAULT_PARAMS, **(params or {})})
        self._model = None
        self._regressors_used: list[str] = []
        self._residual_std: float = 0.0

    def _build_holidays(self, df: pd.DataFrame) -> Optional[pd.DataFrame]:
        """Build a Prophet-format holidays DataFrame from the feature matrix."""
        if "is_public_holiday" not in df.columns or "holiday_name" not in df.columns:
            return None
        hol = df[df["is_public_holiday"] == True][["ds", "holiday_name"]].copy()
        if hol.empty:
            return None
        hol = hol.rename(columns={"holiday_name": "holiday"})
        hol["lower_window"] = 0
        hol["upper_window"] = 1
        return hol.dropna(subset=["holiday"])

    def fit(self, df: pd.DataFrame) -> "ProphetForecaster":
        from prophet import Prophet

        self._validate_df(df)
        train = df[df["y"].notna()].copy()
        train["ds"] = pd.to_datetime(train["ds"])
        train["y"]  = train["y"].astype(float)

        # Determine which regressors are available and non-null
        self._regressors_used = [
            r for r in PROPHET_REGRESSORS
            if r in train.columns and train[r].notna().sum() > len(train) * 0.5
        ]

        holidays = self._build_holidays(train)

        model_kwargs = {k: v for k, v in self.params.items()
                        if k not in ("mcmc_samples",)}
        model_kwargs["interval_width"] = 0.80

        self._model = Prophet(holidays=holidays, **model_kwargs)

        for reg in self._regressors_used:
            self._model.add_regressor(reg, standardize=True)

        fit_df = train[["ds", "y"] + self._regressors_used].copy()
        # Fill regressor NaNs with 0 for fitting
        fit_df[self._regressors_used] = fit_df[self._regressors_used].fillna(0)

        self._model.fit(fit_df)

        # Compute residuals for 95% CI (Prophet native is 80%)
        in_sample = self._model.predict(fit_df)
        residuals = train["y"].values - in_sample["yhat"].values
        self._residual_std = float(np.std(residuals))

        self.is_fitted       = True
        self.feature_names_  = ["ds", "y"] + self._regressors_used
        self.train_start_    = train["ds"].min().date()
        self.train_end_      = train["ds"].max().date()
        self.training_rows_  = len(train)

        log.info(
            "prophet.fitted",
            rows=self.training_rows_,
            regressors=self._regressors_used,
            train_end=str(self.train_end_),
        )
        return self

    def predict(self, df: pd.DataFrame) -> ForecastOutput:
        if not self.is_fitted:
            raise RuntimeError("Model must be fitted before calling predict()")

        pred_df = df[["ds"] + [r for r in self._regressors_used if r in df.columns]].copy()
        pred_df["ds"] = pd.to_datetime(pred_df["ds"])

        # Fill missing regressors with 0
        for reg in self._regressors_used:
            if reg not in pred_df.columns:
                pred_df[reg] = 0
        pred_df[self._regressors_used] = pred_df[self._regressors_used].fillna(0)

        forecast = self._model.predict(pred_df)

        yhat      = forecast["yhat"].values
        z95_scale = 1.960 / 1.282  # scale 80% CI to 95%

        lower_80 = forecast["yhat_lower"].values
        upper_80 = forecast["yhat_upper"].values
        lower_95 = yhat - (yhat - lower_80) * z95_scale
        upper_95 = yhat + (upper_80 - yhat) * z95_scale

        components = {}
        for comp in ("trend", "weekly", "yearly", "holidays"):
            if comp in forecast.columns:
                components[comp] = forecast[comp].values

        return ForecastOutput(
            ds=forecast["ds"],
            predicted_revenue=yhat,
            lower_80=lower_80,
            upper_80=upper_80,
            lower_95=lower_95,
            upper_95=upper_95,
            components=components,
        )

    def get_params(self) -> dict:
        return {**self.params, "regressors_used": self._regressors_used}
