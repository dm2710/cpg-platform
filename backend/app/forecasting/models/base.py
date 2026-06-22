"""
Base forecasting model interface.

All models implement:
  fit(df)        — train on a DataFrame with columns ds + y + features
  predict(df)    — return predictions with confidence intervals
  evaluate(df)   — return metrics dict on a hold-out DataFrame
  get_params()   — return hyperparameters dict for registry
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

import numpy as np
import pandas as pd


@dataclass
class ForecastOutput:
    """Standardised forecast output from any model."""
    ds:                pd.Series                    # forecast dates
    predicted_revenue: np.ndarray                   # point forecast
    lower_80:          Optional[np.ndarray] = None
    upper_80:          Optional[np.ndarray] = None
    lower_95:          Optional[np.ndarray] = None
    upper_95:          Optional[np.ndarray] = None
    components:        dict[str, np.ndarray] = field(default_factory=dict)

    def to_dataframe(self) -> pd.DataFrame:
        df = pd.DataFrame({
            "ds":                self.ds,
            "predicted_revenue": np.maximum(self.predicted_revenue, 0),
        })
        for col, arr in [
            ("lower_80", self.lower_80), ("upper_80", self.upper_80),
            ("lower_95", self.lower_95), ("upper_95", self.upper_95),
        ]:
            df[col] = np.maximum(arr, 0) if arr is not None else None

        for name, arr in self.components.items():
            df[f"component_{name}"] = arr

        return df


@dataclass
class ModelMetrics:
    """Evaluation metrics on a hold-out set."""
    mae:   float
    mape:  float   # %
    rmse:  float
    smape: float   # %
    r2:    float
    bias:  float   # mean(predicted - actual)
    n:     int

    def to_dict(self) -> dict[str, float]:
        return {
            "mae": round(self.mae, 4),   "mape": round(self.mape, 4),
            "rmse": round(self.rmse, 4), "smape": round(self.smape, 4),
            "r2": round(self.r2, 6),     "bias": round(self.bias, 4),
            "n": self.n,
        }


class BaseForecaster(ABC):
    """Abstract base for all forecasting models."""

    name: str = "base"

    def __init__(self, params: Optional[dict] = None):
        self.params: dict[str, Any] = params or {}
        self.is_fitted: bool = False
        self.feature_names_: list[str] = []
        self.train_start_: Optional[date] = None
        self.train_end_:   Optional[date] = None
        self.training_rows_: int = 0

    @abstractmethod
    def fit(self, df: pd.DataFrame) -> "BaseForecaster":
        """Train on df with columns: ds, y, [feature columns]."""
        ...

    @abstractmethod
    def predict(self, df: pd.DataFrame) -> ForecastOutput:
        """
        Generate forecasts.
        df may include future rows (y = NaN) and/or historical rows.
        Only rows with y = NaN are forecasted; historical rows provide
        covariate values for future prediction.
        """
        ...

    def evaluate(self, df: pd.DataFrame) -> ModelMetrics:
        """Evaluate on a hold-out set (rows where y is not NaN)."""
        eval_df = df[df["y"].notna()].copy()
        if eval_df.empty:
            raise ValueError("No rows with actual values for evaluation")

        output = self.predict(eval_df)
        actuals   = eval_df["y"].values
        predicted = output.predicted_revenue

        return compute_metrics(actuals, predicted)

    def get_params(self) -> dict:
        return self.params

    def _validate_df(self, df: pd.DataFrame) -> None:
        if "ds" not in df.columns:
            raise ValueError("DataFrame must have a 'ds' column")
        if "y" not in df.columns:
            raise ValueError("DataFrame must have a 'y' column")
        if df.empty:
            raise ValueError("Cannot fit on empty DataFrame")


def compute_metrics(actuals: np.ndarray, predicted: np.ndarray) -> ModelMetrics:
    """Compute standard forecasting metrics."""
    actuals   = np.array(actuals, dtype=float)
    predicted = np.array(predicted, dtype=float)

    mask = ~(np.isnan(actuals) | np.isnan(predicted))
    actuals   = actuals[mask]
    predicted = predicted[mask]

    if len(actuals) == 0:
        return ModelMetrics(0, 0, 0, 0, 0, 0, 0)

    errors  = actuals - predicted
    abs_err = np.abs(errors)

    mae  = float(np.mean(abs_err))
    rmse = float(np.sqrt(np.mean(errors ** 2)))
    bias = float(np.mean(errors))

    # MAPE — skip near-zero actuals
    nonzero = actuals != 0
    mape  = float(np.mean(abs_err[nonzero] / np.abs(actuals[nonzero])) * 100) if nonzero.any() else 0.0
    smape_vals = 2 * abs_err / (np.abs(actuals) + np.abs(predicted) + 1e-8)
    smape = float(np.mean(smape_vals) * 100)

    # R²
    ss_res = np.sum(errors ** 2)
    ss_tot = np.sum((actuals - np.mean(actuals)) ** 2)
    r2     = float(1 - ss_res / (ss_tot + 1e-8))

    return ModelMetrics(mae=mae, mape=mape, rmse=rmse, smape=smape, r2=r2, bias=bias, n=len(actuals))


def compute_prediction_intervals(
    predicted: np.ndarray,
    residual_std: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Simple Gaussian PI from residual std (fallback when model doesn't provide native CI)."""
    z80 = 1.282
    z95 = 1.960
    lower_80 = predicted - z80 * residual_std
    upper_80 = predicted + z80 * residual_std
    lower_95 = predicted - z95 * residual_std
    upper_95 = predicted + z95 * residual_std
    return lower_80, upper_80, lower_95, upper_95
