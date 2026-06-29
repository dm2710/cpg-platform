"""
Context builders — assemble grounded data from PostgreSQL before
every LLM call. The LLM never invents numbers; every figure in its
response must trace back to a value in one of these context dicts.

Four builders:
  build_revenue_context()  — totals, trend, YoY, category/region breakdown
  build_forecast_context() — forecast horizon, CI bands, model accuracy
  build_signal_context()   — active promos, campaigns, competitor pricing
  build_driver_context()   — per-category and per-region contribution ranking
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger

log = get_logger(__name__)


def _filters(category_id: Optional[int], region_id: Optional[int]) -> tuple[list[str], dict]:
    flt, params = [], {}
    if category_id is not None:
        flt.append("category_id = :cat")
        params["cat"] = category_id
    if region_id is not None:
        flt.append("region_id = :region")
        params["region"] = region_id
    return flt, params


def resolve_segment_label(db: Session, category_id: Optional[int], region_id: Optional[int]) -> str:
    """Human-readable segment label, e.g. 'Dairy / North America'."""
    cat = "All categories"
    reg = "All regions"
    if category_id:
        v = db.execute(
            text("SELECT category_name FROM dim_product_category WHERE category_id=:id"),
            {"id": category_id},
        ).scalar()
        if v:
            cat = v
    if region_id:
        v = db.execute(
            text("SELECT region_name FROM dim_region WHERE region_id=:id"),
            {"id": region_id},
        ).scalar()
        if v:
            reg = v
    return f"{cat} / {reg}"


# ── Revenue context ───────────────────────────────────────

def build_revenue_context(
    db: Session,
    category_id: Optional[int],
    region_id: Optional[int],
    lookback_days: int = 90,
) -> dict:
    """Recent revenue actuals with trend, YoY, and dimension breakdowns."""
    end_date    = date.today()
    start_date  = end_date - timedelta(days=lookback_days)
    prior_start = start_date - timedelta(days=lookback_days)

    flt, params = _filters(category_id, region_id)
    where    = f"WHERE {' AND '.join(flt)}" if flt else ""
    and_or   = "AND" if flt else "WHERE"

    series = db.execute(
        text(f"""
            SELECT agg_date, total_revenue, total_quantity, txn_count
            FROM agg_revenue_daily
            {where} {and_or} agg_date BETWEEN :start AND :end
            ORDER BY agg_date
        """),
        {**params, "start": start_date, "end": end_date},
    ).mappings().all()

    prior_total = db.execute(
        text(f"""
            SELECT COALESCE(SUM(total_revenue), 0)
            FROM agg_revenue_daily
            {where} {and_or} agg_date BETWEEN :start AND :end
        """),
        {**params, "start": prior_start, "end": start_date - timedelta(days=1)},
    ).scalar() or 0

    current_total = sum(float(r["total_revenue"]) for r in series)
    yoy_pct = round((current_total - float(prior_total)) / float(prior_total) * 100, 2) if prior_total else None

    last_7  = sum(float(r["total_revenue"]) for r in series[-7:])    if len(series) >= 7  else None
    last_28 = sum(float(r["total_revenue"]) for r in series[-28:])   if len(series) >= 28 else None
    prev_7  = sum(float(r["total_revenue"]) for r in series[-14:-7])  if len(series) >= 14 else None
    prev_28 = sum(float(r["total_revenue"]) for r in series[-56:-28]) if len(series) >= 56 else None

    cat_rows = db.execute(text("""
        SELECT dpc.category_name, SUM(a.total_revenue) AS revenue
        FROM agg_revenue_daily a
        JOIN dim_product_category dpc ON dpc.category_id = a.category_id
        WHERE a.agg_date BETWEEN :s AND :e
        GROUP BY dpc.category_name ORDER BY revenue DESC LIMIT 5
    """), {"s": start_date, "e": end_date}).mappings().all()

    reg_rows = db.execute(text("""
        SELECT dr.region_name, SUM(a.total_revenue) AS revenue
        FROM agg_revenue_daily a
        JOIN dim_region dr ON dr.region_id = a.region_id
        WHERE a.agg_date BETWEEN :s AND :e
        GROUP BY dr.region_name ORDER BY revenue DESC LIMIT 5
    """), {"s": start_date, "e": end_date}).mappings().all()

    return {
        "period":               {"start": str(start_date), "end": str(end_date), "days": lookback_days},
        "total_revenue":        round(current_total, 2),
        "prior_period_revenue": round(float(prior_total), 2),
        "yoy_change_pct":       yoy_pct,
        "trend_7d_pct":         round((last_7 - prev_7) / prev_7 * 100, 2) if last_7 and prev_7 else None,
        "trend_28d_pct":        round((last_28 - prev_28) / prev_28 * 100, 2) if last_28 and prev_28 else None,
        "top_categories":       [{"category": r["category_name"], "revenue": round(float(r["revenue"]), 2)} for r in cat_rows],
        "top_regions":          [{"region": r["region_name"], "revenue": round(float(r["revenue"]), 2)} for r in reg_rows],
        "daily_series_last_30": [{"date": str(r["agg_date"]), "revenue": round(float(r["total_revenue"]), 2)} for r in series[-30:]],
    }


# ── Forecast context ──────────────────────────────────────

def build_forecast_context(
    db: Session,
    category_id: Optional[int],
    region_id: Optional[int],
    horizon_days: int = 30,
) -> dict:
    """Forecast horizon with CI bands and deployed-model accuracy."""
    from app.forecasting.features.engineer import segment_key

    seg = segment_key(category_id, region_id)

    def _fetch_forecasts(sk: str) -> list:
        return db.execute(text("""
            SELECT DISTINCT ON (forecast_date)
                   forecast_date, model_name, predicted_revenue,
                   lower_80, upper_80, actual_revenue, error_pct
            FROM forecast_results
            WHERE segment_key = :seg
              AND forecast_date BETWEEN CURRENT_DATE AND CURRENT_DATE + :horizon
            ORDER BY forecast_date, generated_at DESC
        """), {"seg": sk, "horizon": horizon_days}).mappings().all()

    # Try exact segment first; fall back to global if no rows
    forecasts = _fetch_forecasts(seg)
    used_seg = seg
    if not forecasts and seg != "global":
        forecasts = _fetch_forecasts("global")
        used_seg = "global"

    model_info = db.execute(text("""
        SELECT model_name, mape, mae, trained_at
        FROM model_registry
        WHERE segment_key = :seg AND status = 'deployed'
        ORDER BY mape ASC NULLS LAST LIMIT 1
    """), {"seg": used_seg}).mappings().first()

    # Fall back to any deployed model if exact segment has none
    if not model_info:
        model_info = db.execute(text("""
            SELECT model_name, mape, mae, trained_at
            FROM model_registry
            WHERE status = 'deployed'
            ORDER BY mape ASC NULLS LAST LIMIT 1
        """)).mappings().first()

    recent_acc = db.execute(text("""
        SELECT AVG(fa.mape) AS avg_mape, AVG(fa.bias) AS avg_bias, COUNT(*) AS n
        FROM forecast_accuracy fa
        JOIN model_registry mr ON mr.model_id = fa.model_id
        WHERE fa.segment_key = :seg
          AND fa.evaluation_date >= CURRENT_DATE - 30
          AND mr.status = 'deployed'
    """), {"seg": used_seg}).mappings().first()

    total = sum(float(r["predicted_revenue"]) for r in forecasts)

    return {
        "segment_key":            used_seg,
        "horizon_days":           horizon_days,
        "total_forecast_revenue": round(total, 2),
        "avg_daily_forecast":     round(total / max(len(forecasts), 1), 2),
        "num_forecast_days":      len(forecasts),
        "model": {
            "name":       model_info["model_name"]      if model_info else None,
            "mape":       float(model_info["mape"])     if model_info and model_info["mape"]  else None,
            "mae":        float(model_info["mae"])      if model_info and model_info["mae"]   else None,
            "trained_at": str(model_info["trained_at"]) if model_info else None,
        },
        "recent_accuracy": {
            "avg_mape": round(float(recent_acc["avg_mape"]), 2) if recent_acc and recent_acc["avg_mape"] else None,
            "avg_bias": round(float(recent_acc["avg_bias"]), 2) if recent_acc and recent_acc["avg_bias"] else None,
            "n_points": int(recent_acc["n"]) if recent_acc else 0,
        },
        "forecast_series": [
            {
                "date":      str(r["forecast_date"]),
                "predicted": round(float(r["predicted_revenue"]), 2),
                "lower_80":  round(float(r["lower_80"]), 2)  if r["lower_80"]       else None,
                "upper_80":  round(float(r["upper_80"]), 2)  if r["upper_80"]       else None,
                "actual":    round(float(r["actual_revenue"]), 2) if r["actual_revenue"] else None,
                "error_pct": round(float(r["error_pct"]), 2) if r["error_pct"]      else None,
            }
            for r in forecasts
        ],
    }


# ── Signal context ────────────────────────────────────────

def build_signal_context(
    db: Session,
    category_id: Optional[int],
    region_id: Optional[int],
) -> dict:
    """Active promos, campaigns, and competitive pricing signals."""
    today = date.today()

    promos = db.execute(text("""
        SELECT promo_name, promo_type, discount_pct, start_date, end_date, channel
        FROM promo_windows
        WHERE :today BETWEEN start_date AND end_date
          AND (category_id = :cat OR category_id IS NULL)
          AND (region_id   = :region OR region_id IS NULL)
        ORDER BY discount_pct DESC NULLS LAST LIMIT 5
    """), {"today": today, "cat": category_id, "region": region_id}).mappings().all()

    campaigns = db.execute(text("""
        SELECT campaign_name, channel, campaign_type, budget_usd, start_date, end_date
        FROM marketing_campaigns
        WHERE :today BETWEEN start_date AND end_date
          AND (target_category_id = :cat OR target_category_id IS NULL)
          AND (target_region_id   = :region OR target_region_id IS NULL)
        ORDER BY budget_usd DESC NULLS LAST LIMIT 5
    """), {"today": today, "cat": category_id, "region": region_id}).mappings().all()

    comp_pricing = db.execute(text("""
        SELECT competitor_name, AVG(price_index) AS avg_index, MAX(snapshot_date) AS latest
        FROM competitor_pricing
        WHERE snapshot_date >= :start
          AND (category_id = :cat OR category_id IS NULL)
          AND (region_id   = :region OR region_id IS NULL)
        GROUP BY competitor_name ORDER BY avg_index ASC
    """), {"start": today - timedelta(days=30), "cat": category_id, "region": region_id}).mappings().all()

    return {
        "as_of_date": str(today),
        "active_promos": [
            {
                "name":         r["promo_name"],
                "type":         r["promo_type"],
                "discount_pct": float(r["discount_pct"]) if r["discount_pct"] else None,
                "channel":      r["channel"],
                "ends":         str(r["end_date"]),
            }
            for r in promos
        ],
        "active_campaigns": [
            {
                "name":       r["campaign_name"],
                "channel":    r["channel"],
                "type":       r["campaign_type"],
                "budget_usd": float(r["budget_usd"]) if r["budget_usd"] else None,
                "ends":       str(r["end_date"]),
            }
            for r in campaigns
        ],
        "competitor_pricing": [
            {
                "competitor":  r["competitor_name"],
                "price_index": round(float(r["avg_index"]), 3) if r["avg_index"] else None,
                "as_of":       str(r["latest"]),
            }
            for r in comp_pricing
        ],
    }


# ── Driver context (category/region contribution ranking) ─

def build_driver_context(
    db: Session,
    category_id: Optional[int],
    region_id: Optional[int],
    lookback_days: int = 90,
) -> dict:
    """
    Per-category and per-region revenue contribution with trend,
    used by RevenueDriverEngine to rank the top drivers.
    """
    end   = date.today()
    start = end - timedelta(days=lookback_days)
    mid   = end - timedelta(days=lookback_days // 2)

    cat_rows = db.execute(text("""
        SELECT
            dpc.category_name,
            SUM(a.total_revenue) AS total_revenue,
            SUM(CASE WHEN a.agg_date >= :mid THEN a.total_revenue ELSE 0 END) AS recent_half,
            SUM(CASE WHEN a.agg_date <  :mid THEN a.total_revenue ELSE 0 END) AS earlier_half
        FROM agg_revenue_daily a
        JOIN dim_product_category dpc ON dpc.category_id = a.category_id
        WHERE a.agg_date BETWEEN :start AND :end
        GROUP BY dpc.category_name
        ORDER BY total_revenue DESC
    """), {"start": start, "end": end, "mid": mid}).mappings().all()

    reg_rows = db.execute(text("""
        SELECT
            dr.region_name,
            SUM(a.total_revenue) AS total_revenue,
            SUM(CASE WHEN a.agg_date >= :mid THEN a.total_revenue ELSE 0 END) AS recent_half,
            SUM(CASE WHEN a.agg_date <  :mid THEN a.total_revenue ELSE 0 END) AS earlier_half
        FROM agg_revenue_daily a
        JOIN dim_region dr ON dr.region_id = a.region_id
        WHERE a.agg_date BETWEEN :start AND :end
        GROUP BY dr.region_name
        ORDER BY total_revenue DESC
    """), {"start": start, "end": end, "mid": mid}).mappings().all()

    grand_total = sum(float(r["total_revenue"]) for r in cat_rows) or 1

    def _build(rows: list, label_key: str) -> list[dict]:
        out = []
        for r in rows:
            rev    = float(r["total_revenue"])
            recent = float(r["recent_half"])
            early  = float(r["earlier_half"])
            trend_pct = round((recent - early) / early * 100, 1) if early else None
            out.append({
                "name":            r[label_key],
                "revenue":         round(rev, 2),
                "pct_of_total":    round(rev / grand_total * 100, 1),
                "trend_pct":       trend_pct,
                "trend_direction": ("growing" if (trend_pct or 0) > 2 else
                                    "declining" if (trend_pct or 0) < -2 else "flat"),
            })
        return out

    return {
        "period":             {"start": str(start), "end": str(end), "days": lookback_days},
        "category_breakdown": _build(cat_rows, "category_name"),
        "region_breakdown":   _build(reg_rows, "region_name"),
    }
