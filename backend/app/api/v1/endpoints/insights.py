"""
Phase 3 — AI Insights API router.

POST /insights/trend                — trend summarization
POST /insights/root-cause           — root cause analysis
POST /insights/forecast/explain     — forecast explanation
POST /insights/drivers              — revenue driver analysis
POST /insights/executive-summary    — executive summary generation
GET  /insights/log                  — LLM call audit log

All five engines are grounded in live PostgreSQL data before any
DeepSeek call is made, and all responses are cached per-type TTL
to avoid redundant LLM spend.
"""

import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.logging import get_logger
from app.forecasting.features.engineer import segment_key
from app.security.deps import CurrentUser, require_permission
from app.security.rbac import Permission
from app.insights.cache.insight_cache import cache_get, cache_set
from app.insights.engines.insight_engines import (
    ExecutiveSummaryEngine,
    ForecastExplanationEngine,
    InsightResult,
    RevenueDriverAnalysisEngine,
    RootCauseAnalysisEngine,
    TrendSummarizationEngine,
)
from app.schemas.insights import (
    DriverRequest, DriverResponse,
    ExecutiveRequest, ExecutiveResponse,
    ForecastExplainRequest, ForecastExplainResponse,
    InsightLogOut,
    RootCauseRequest, RootCauseResponse,
    TrendRequest, TrendResponse,
)

router = APIRouter()
log    = get_logger(__name__)


def _audit(db: Session, result: InsightResult, question: Optional[str] = None) -> None:
    """Write a permanent record of this LLM call to insight_log."""
    try:
        db.execute(
            text("""
                INSERT INTO insight_log
                    (insight_type, segment_key, question, model_used,
                     insight_text, structured_data, confidence,
                     tokens_total, latency_ms, from_cache, status, triggered_by)
                VALUES
                    (:itype, :seg, :q, :model,
                     :text, CAST(:data AS jsonb), :conf,
                     :tokens, :latency, :cache, 'success', 'api')
            """),
            {
                "itype":   result.insight_type,
                "seg":     result.structured_data.get("segment_key", "unknown"),
                "q":       question,
                "model":   result.model_used,
                "text":    result.insight_text,
                "data":    json.dumps(result.structured_data, default=str),
                "conf":    result.confidence,
                "tokens":  result.tokens_total,
                "latency": result.latency_ms,
                "cache":   result.from_cache,
            },
        )
        db.commit()
    except Exception as exc:
        log.warning("audit.failed", error=str(exc))


# ── 1. Trend Summarization ────────────────────────────────

@router.post("/trend", response_model=TrendResponse, summary="Revenue trend summarization")
async def trend_summary(
    req: TrendRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.TRIGGER_INSIGHT)),
):
    """
    3-5 sentence revenue trend narrative covering period total, direction
    versus prior period, YoY change, top drivers, and any notable pattern.
    Cached for 4 hours per segment.
    """
    seg    = segment_key(req.category_id, req.region_id)
    params = {"lookback_days": req.lookback_days}

    hit = cache_get(db, "trend", seg, params)
    if hit:
        return TrendResponse(**hit.to_dict())

    result = await TrendSummarizationEngine(db).generate(
        req.category_id, req.region_id, req.lookback_days
    )
    cache_set(db, "trend", seg, params, result,
              category_id=req.category_id, region_id=req.region_id)
    _audit(db, result)
    return TrendResponse(**result.to_dict())


# ── 2. Root Cause Analysis ────────────────────────────────

@router.post("/root-cause", response_model=RootCauseResponse, summary="Root cause analysis")
async def root_cause(
    req: RootCauseRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.TRIGGER_INSIGHT)),
):
    """
    Structured root cause analysis with PRIMARY DRIVER, CONTRIBUTING
    FACTORS, EVIDENCE, and DATA GAPS sections. Provide a short
    description of the observed change, e.g.
    "Revenue dropped 18% in the last two weeks".
    Cached for 3 hours.
    """
    seg    = segment_key(req.category_id, req.region_id)
    params = {"change": req.change_description[:80], "lookback": req.lookback_days}

    hit = cache_get(db, "root_cause", seg, params)
    if hit:
        return RootCauseResponse(**hit.to_dict())

    result = await RootCauseAnalysisEngine(db).generate(
        req.change_description, req.category_id, req.region_id, req.lookback_days
    )
    cache_set(db, "root_cause", seg, params, result,
              question=req.change_description,
              category_id=req.category_id, region_id=req.region_id)
    _audit(db, result, question=req.change_description)
    return RootCauseResponse(**result.to_dict())


# ── 3. Forecast Explanation ───────────────────────────────

@router.post("/forecast/explain", response_model=ForecastExplainResponse, summary="Forecast explanation")
async def explain_forecast(
    req: ForecastExplainRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.TRIGGER_INSIGHT)),
):
    """
    Business-friendly forecast explanation: total and daily run-rate,
    80% confidence band in dollar terms, model MAPE, and any active
    promotions/campaigns that may influence the outcome.
    Cached for 2 hours.
    """
    seg    = segment_key(req.category_id, req.region_id)
    params = {"horizon": req.horizon_days}

    hit = cache_get(db, "forecast", seg, params)
    if hit:
        return ForecastExplainResponse(**hit.to_dict())

    result = await ForecastExplanationEngine(db).generate(
        req.category_id, req.region_id, req.horizon_days
    )
    cache_set(db, "forecast", seg, params, result,
              category_id=req.category_id, region_id=req.region_id)
    _audit(db, result)
    return ForecastExplainResponse(**result.to_dict())


# ── 4. Revenue Driver Analysis ────────────────────────────

@router.post("/drivers", response_model=DriverResponse, summary="Revenue driver analysis")
async def revenue_drivers(
    req: DriverRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.TRIGGER_INSIGHT)),
):
    """
    Ranks the top 3 revenue drivers (category or region) with dollar
    contribution, percentage of total, and trend direction.
    Cached for 4 hours.
    """
    seg    = segment_key(req.category_id, req.region_id)
    params = {"lookback": req.lookback_days}

    hit = cache_get(db, "driver", seg, params)
    if hit:
        return DriverResponse(**hit.to_dict())

    result = await RevenueDriverAnalysisEngine(db).generate(
        req.category_id, req.region_id, req.lookback_days
    )
    cache_set(db, "driver", seg, params, result,
              category_id=req.category_id, region_id=req.region_id)
    _audit(db, result)
    return DriverResponse(**result.to_dict())


# ── 5. Executive Summary ──────────────────────────────────

@router.post("/executive-summary", response_model=ExecutiveResponse, summary="Executive summary generation")
async def executive_summary(
    req: ExecutiveRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.TRIGGER_INSIGHT)),
):
    """
    Six-section board-level summary: PERFORMANCE HEADLINE, WHAT HAPPENED,
    FORECAST, TOP RISK, TOP OPPORTUNITY, RECOMMENDED ACTION.
    Cached for 6 hours.
    """
    seg    = segment_key(req.category_id, req.region_id)
    params = {"lookback": req.lookback_days, "horizon": req.horizon_days}

    hit = cache_get(db, "executive", seg, params)
    if hit:
        return ExecutiveResponse(**hit.to_dict())

    result = await ExecutiveSummaryEngine(db).generate(
        req.category_id, req.region_id, req.lookback_days, req.horizon_days
    )
    cache_set(db, "executive", seg, params, result,
              category_id=req.category_id, region_id=req.region_id)
    _audit(db, result)
    return ExecutiveResponse(**result.to_dict())


# ── Audit log ─────────────────────────────────────────────

@router.get("/log", response_model=list[InsightLogOut], summary="LLM call audit log")
async def insight_log(
    insight_type: Optional[str] = None,
    limit:        int           = Query(default=50, ge=1, le=500),
    db:           Session       = Depends(get_db),
):
    """Permanent record of every DeepSeek call with tokens, latency, and confidence."""
    filters, params = [], {"limit": limit}
    if insight_type:
        filters.append("insight_type=:itype")
        params["itype"] = insight_type
    where = f"WHERE {' AND '.join(filters)}" if filters else ""

    rows = db.execute(
        text(f"""
            SELECT log_id, insight_type, segment_key, question, confidence,
                   model_used, tokens_total, latency_ms, from_cache, status, requested_at
            FROM insight_log {where}
            ORDER BY requested_at DESC LIMIT :limit
        """),
        params,
    ).mappings().all()
    return [InsightLogOut(**dict(r)) for r in rows]
