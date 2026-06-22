"""
Feature engineering for CPG demand forecasting.

Builds the feature matrix from agg_revenue_daily joined with
calendar, promo, campaign, competitor, weather, and demographic signals.

Features produced:
  Calendar    — dow, month, quarter, is_weekend, is_holiday, retail_season
  Lag         — 7d, 14d, 28d, 90d, 365d revenue lags
  Rolling     — 7d/14d/28d mean, std, min, max
  YoY         — same-period prior year revenue and growth %
  Trend       — % change over 7d and 28d windows
  Signals     — promo count, max discount, campaign spend, price index
  Contextual  — weather, demographics
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger

log = get_logger(__name__)

# Minimum rows required to compute all lag/rolling features
MIN_ROWS_FOR_LAGS = 30
LAG_DAYS = [7, 14, 28, 90, 365]
ROLLING_WINDOWS = [7, 14, 28]


# ── Raw data loaders ──────────────────────────────────────

def load_revenue_series(
    db: Session,
    category_id: Optional[int],
    region_id: Optional[int],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> pd.DataFrame:
    """Load daily revenue from agg_revenue_daily for a single segment.

    When category_id or region_id is None the query aggregates across all
    values of that dimension, so each date always has exactly one row.
    """
    filters = []
    params: dict = {}

    if category_id is not None:
        filters.append("category_id = :cat")
        params["cat"] = category_id
    if region_id is not None:
        filters.append("region_id = :region")
        params["region"] = region_id
    if start_date:
        filters.append("agg_date >= :start")
        params["start"] = start_date
    if end_date:
        filters.append("agg_date <= :end")
        params["end"] = end_date

    where = f"WHERE {' AND '.join(filters)}" if filters else ""

    df = pd.read_sql(
        text(f"""
            SELECT agg_date AS ds,
                   SUM(total_revenue)  AS y,
                   SUM(total_quantity) AS quantity
            FROM agg_revenue_daily
            {where}
            GROUP BY agg_date
            ORDER BY agg_date
        """),
        db.bind,
        params=params,
    )
    df["ds"] = pd.to_datetime(df["ds"])
    df["y"]  = df["y"].astype(float)
    return df


def load_calendar_features(db: Session, dates: list[date]) -> pd.DataFrame:
    """Load calendar attributes for a list of dates."""
    if not dates:
        return pd.DataFrame()

    placeholders = ", ".join(f":d{i}" for i in range(len(dates)))
    params = {f"d{i}": d for i, d in enumerate(dates)}

    df = pd.read_sql(
        text(f"""
            SELECT cal_date AS ds, day_of_week, week_of_year, month, quarter, year,
                   is_weekend, is_public_holiday, holiday_name, retail_season,
                   EXTRACT(DAY FROM cal_date)::int AS day_of_month,
                   (EXTRACT(DAY FROM cal_date) = 1)::bool AS is_month_start,
                   (cal_date = DATE_TRUNC('month', cal_date) + INTERVAL '1 month - 1 day')::bool AS is_month_end,
                   (EXTRACT(DAY FROM cal_date) = 1 AND EXTRACT(MONTH FROM cal_date) IN (1,4,7,10))::bool AS is_quarter_start,
                   (cal_date = DATE_TRUNC('quarter', cal_date) + INTERVAL '3 months - 1 day')::bool AS is_quarter_end
            FROM dim_calendar
            WHERE cal_date IN ({placeholders})
        """),
        db.bind,
        params=params,
    )
    df["ds"] = pd.to_datetime(df["ds"])
    return df


def load_promo_signals(
    db: Session,
    category_id: Optional[int],
    region_id: Optional[int],
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Active promo count and max discount per day."""
    df = pd.read_sql(
        text("""
            SELECT
                d.cal_date AS ds,
                COUNT(p.promo_id)      AS active_promo_count,
                MAX(p.discount_pct)    AS max_discount_pct
            FROM dim_calendar d
            LEFT JOIN promo_windows p
                ON d.cal_date BETWEEN p.start_date AND p.end_date
               AND (p.category_id = :cat OR p.category_id IS NULL)
               AND (p.region_id   = :region OR p.region_id IS NULL)
            WHERE d.cal_date BETWEEN :start AND :end
            GROUP BY d.cal_date
            ORDER BY d.cal_date
        """),
        db.bind,
        params={"cat": category_id, "region": region_id, "start": start_date, "end": end_date},
    )
    df["ds"] = pd.to_datetime(df["ds"])
    df["active_promo_count"] = df["active_promo_count"].fillna(0).astype(int)
    df["max_discount_pct"]   = df["max_discount_pct"].fillna(0.0)
    return df


def load_campaign_signals(
    db: Session,
    category_id: Optional[int],
    region_id: Optional[int],
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Daily campaign spend (budget / campaign_length) per day."""
    df = pd.read_sql(
        text("""
            SELECT
                d.cal_date AS ds,
                COALESCE(SUM(
                    c.budget_usd / NULLIF(c.end_date - c.start_date + 1, 0)
                ), 0) AS daily_campaign_spend
            FROM dim_calendar d
            LEFT JOIN marketing_campaigns c
                ON d.cal_date BETWEEN c.start_date AND c.end_date
               AND (c.target_category_id = :cat OR c.target_category_id IS NULL)
               AND (c.target_region_id   = :region OR c.target_region_id IS NULL)
            WHERE d.cal_date BETWEEN :start AND :end
            GROUP BY d.cal_date
            ORDER BY d.cal_date
        """),
        db.bind,
        params={"cat": category_id, "region": region_id, "start": start_date, "end": end_date},
    )
    df["ds"] = pd.to_datetime(df["ds"])
    df["daily_campaign_spend"] = df["daily_campaign_spend"].fillna(0.0)
    return df


def load_competitor_signals(
    db: Session,
    category_id: Optional[int],
    region_id: Optional[int],
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Most recent competitor price index per day (forward-fill)."""
    df = pd.read_sql(
        text("""
            SELECT
                d.cal_date AS ds,
                AVG(cp.price_index) AS competitor_price_index
            FROM dim_calendar d
            LEFT JOIN competitor_pricing cp
                ON cp.snapshot_date <= d.cal_date
               AND (cp.category_id = :cat OR cp.category_id IS NULL)
               AND (cp.region_id   = :region OR cp.region_id IS NULL)
            WHERE d.cal_date BETWEEN :start AND :end
            GROUP BY d.cal_date
            ORDER BY d.cal_date
        """),
        db.bind,
        params={"cat": category_id, "region": region_id, "start": start_date, "end": end_date},
    )
    df["ds"] = pd.to_datetime(df["ds"])
    df["competitor_price_index"] = df["competitor_price_index"].ffill()
    return df


def load_weather_signals(
    db: Session,
    region_id: Optional[int],
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    if region_id is None:
        return pd.DataFrame()
    df = pd.read_sql(
        text("""
            SELECT weather_date AS ds, avg_temp_c, precipitation_mm, is_extreme_weather
            FROM weather_daily
            WHERE region_id = :region
              AND weather_date BETWEEN :start AND :end
            ORDER BY weather_date
        """),
        db.bind,
        params={"region": region_id, "start": start_date, "end": end_date},
    )
    df["ds"] = pd.to_datetime(df["ds"])
    return df


def load_demographics(
    db: Session,
    region_id: Optional[int],
    as_of_year: int,
) -> dict:
    if region_id is None:
        return {}
    row = db.execute(
        text("""
            SELECT median_income_usd, urban_pct, population
            FROM dim_region_demographics
            WHERE region_id = :region AND snapshot_year <= :year
            ORDER BY snapshot_year DESC LIMIT 1
        """),
        {"region": region_id, "year": as_of_year},
    ).mappings().first()
    return dict(row) if row else {}


# ── Lag / rolling feature computation ────────────────────

def add_lag_features(df: pd.DataFrame, target_col: str = "y") -> pd.DataFrame:
    """Add lagged values of target column."""
    df = df.sort_values("ds").copy()
    for lag in LAG_DAYS:
        df[f"lag_{lag}d"] = df[target_col].shift(lag)
    return df


def add_rolling_features(df: pd.DataFrame, target_col: str = "y") -> pd.DataFrame:
    """Add rolling window statistics."""
    df = df.sort_values("ds").copy()
    for window in ROLLING_WINDOWS:
        rolled = df[target_col].shift(1).rolling(window, min_periods=max(1, window // 2))
        df[f"rolling_mean_{window}d"] = rolled.mean()
        df[f"rolling_std_{window}d"]  = rolled.std()
        if window == 28:
            df[f"rolling_min_{window}d"] = rolled.min()
            df[f"rolling_max_{window}d"] = rolled.max()
    return df


def add_yoy_features(df: pd.DataFrame, target_col: str = "y") -> pd.DataFrame:
    """Year-over-year revenue and growth %."""
    df = df.sort_values("ds").copy()
    df["yoy_revenue"]    = df[target_col].shift(365)
    prior                = df["yoy_revenue"].replace(0, np.nan)
    df["yoy_growth_pct"] = ((df[target_col] - prior) / prior * 100).round(4)
    return df


def add_trend_features(df: pd.DataFrame, target_col: str = "y") -> pd.DataFrame:
    """Short and medium-term revenue trend (% change)."""
    df = df.sort_values("ds").copy()
    for window in [7, 28]:
        prior = df[target_col].shift(window).replace(0, np.nan)
        df[f"revenue_trend_{window}d"] = ((df[target_col] - prior) / prior * 100).round(4)
    return df


def add_calendar_encodings(df: pd.DataFrame) -> pd.DataFrame:
    """Cyclical encoding of periodic calendar features."""
    df = df.copy()
    df["sin_dow"]         = np.sin(2 * np.pi * df["day_of_week"] / 7)
    df["cos_dow"]         = np.cos(2 * np.pi * df["day_of_week"] / 7)
    df["sin_month"]       = np.sin(2 * np.pi * df["month"] / 12)
    df["cos_month"]       = np.cos(2 * np.pi * df["month"] / 12)
    df["sin_week"]        = np.sin(2 * np.pi * df["week_of_year"] / 52)
    df["cos_week"]        = np.cos(2 * np.pi * df["week_of_year"] / 52)
    # Retail season ordinal
    season_map = {"Off-Peak": 0, "Spring": 1, "Summer": 2, "Back-to-School": 3, "Holiday": 4}
    df["retail_season_ord"] = df.get("retail_season", pd.Series(dtype=str)).map(season_map).fillna(0)
    return df


# ── Master feature builder ────────────────────────────────

def build_feature_matrix(
    db: Session,
    category_id: Optional[int],
    region_id: Optional[int],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    include_future: bool = False,
    horizon_days: int = 0,
) -> pd.DataFrame:
    """
    Build the complete feature matrix for a (category, region) segment.

    If include_future=True, appends `horizon_days` future rows (no target)
    for use during prediction.
    """
    # 1. Load base revenue series (need extra history for lag features)
    history_start = (start_date - timedelta(days=400)) if start_date else None
    df = load_revenue_series(db, category_id, region_id, history_start, end_date)

    if df.empty:
        log.warning(
            "feature_build.no_data",
            category_id=category_id,
            region_id=region_id,
        )
        return pd.DataFrame()

    actual_end = df["ds"].max().date()

    # 2. Append future skeleton if requested
    if include_future and horizon_days > 0:
        future_dates = [actual_end + timedelta(days=i + 1) for i in range(horizon_days)]
        future_df = pd.DataFrame({"ds": pd.to_datetime(future_dates), "y": np.nan, "quantity": np.nan})
        df = pd.concat([df, future_df], ignore_index=True)

    # 3. Lag and rolling features (require sorted history)
    df = add_lag_features(df)
    df = add_rolling_features(df)
    df = add_yoy_features(df)
    df = add_trend_features(df)

    # 4. Determine the date range for signal loading
    signal_start = df["ds"].min().date()
    signal_end   = df["ds"].max().date()
    date_list    = [d.date() for d in df["ds"]]

    # 5. Calendar features
    cal_df = load_calendar_features(db, date_list)
    if not cal_df.empty:
        df = df.merge(cal_df, on="ds", how="left")

    # 6. Promo signals
    promo_df = load_promo_signals(db, category_id, region_id, signal_start, signal_end)
    if not promo_df.empty:
        df = df.merge(promo_df, on="ds", how="left")

    # 7. Campaign signals
    camp_df = load_campaign_signals(db, category_id, region_id, signal_start, signal_end)
    if not camp_df.empty:
        df = df.merge(camp_df, on="ds", how="left")

    # 8. Competitor signals
    comp_df = load_competitor_signals(db, category_id, region_id, signal_start, signal_end)
    if not comp_df.empty:
        df = df.merge(comp_df, on="ds", how="left")

    # 9. Weather signals
    weather_df = load_weather_signals(db, region_id, signal_start, signal_end)
    if not weather_df.empty:
        df = df.merge(weather_df, on="ds", how="left")

    # 10. Demographics (static per year)
    if not df.empty:
        demo = load_demographics(db, region_id, df["ds"].dt.year.max())
        for k, v in demo.items():
            df[k] = v

    # 11. Calendar encodings (cyclical)
    if "day_of_week" in df.columns:
        df = add_calendar_encodings(df)

    # 12. Trim to requested window (after computing lags on full history)
    if start_date:
        df = df[df["ds"] >= pd.Timestamp(start_date)]

    df = df.reset_index(drop=True)
    log.info(
        "feature_build.complete",
        category_id=category_id,
        region_id=region_id,
        rows=len(df),
        cols=len(df.columns),
    )
    return df


def persist_feature_store(
    df: pd.DataFrame,
    category_id: int,
    region_id: int,
    db: Session,
) -> int:
    """Write computed features to feature_store table (upsert)."""
    if df.empty:
        return 0

    feature_cols = [
        "feature_date", "category_id", "region_id",
        "total_revenue", "total_quantity",
        "day_of_week", "day_of_month", "week_of_year", "month", "quarter", "year",
        "is_weekend", "is_month_start", "is_month_end",
        "is_quarter_start", "is_quarter_end", "is_public_holiday", "retail_season",
        "lag_7d", "lag_14d", "lag_28d", "lag_90d", "lag_365d",
        "rolling_mean_7d", "rolling_mean_14d", "rolling_mean_28d",
        "rolling_std_7d", "rolling_std_28d", "rolling_min_28d", "rolling_max_28d",
        "yoy_revenue", "yoy_growth_pct", "revenue_trend_7d", "revenue_trend_28d",
        "active_promo_count", "max_discount_pct", "daily_campaign_spend",
        "competitor_price_index", "avg_temp_c", "precipitation_mm", "is_extreme_weather",
        "median_income_usd", "urban_pct", "population",
    ]

    out = df.rename(columns={"ds": "feature_date", "y": "total_revenue"}).copy()
    out["category_id"] = category_id
    out["region_id"]   = region_id
    out["feature_date"] = pd.to_datetime(out["feature_date"]).dt.date

    count = 0
    for _, row in out.iterrows():
        row_dict = {}
        for col in feature_cols:
            val = row.get(col)
            row_dict[col] = None if (val is None or (isinstance(val, float) and np.isnan(val))) else val

        db.execute(
            text(f"""
                INSERT INTO feature_store ({', '.join(feature_cols)})
                VALUES ({', '.join(':' + c for c in feature_cols)})
                ON CONFLICT (feature_date, category_id, region_id) DO UPDATE SET
                    {', '.join(f"{c} = EXCLUDED.{c}" for c in feature_cols if c not in
                               ('feature_date', 'category_id', 'region_id'))},
                    computed_at = now()
            """),
            row_dict,
        )
        count += 1

    db.commit()
    return count


def get_segments(db: Session) -> list[tuple[Optional[int], Optional[int]]]:
    """Return all (category_id, region_id) combinations with data."""
    rows = db.execute(
        text("SELECT DISTINCT category_id, region_id FROM agg_revenue_daily ORDER BY 1, 2")
    ).fetchall()
    segments = [(None, None)]  # global segment first
    segments += [(r[0], r[1]) for r in rows]
    return segments


def segment_key(category_id: Optional[int], region_id: Optional[int]) -> str:
    """Stable string key for a segment."""
    if category_id is None and region_id is None:
        return "global"
    return f"cat={category_id or 'all'}|region={region_id or 'all'}"
