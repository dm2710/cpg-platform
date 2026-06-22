"""
Five AI insight engines, all backed by DeepSeek (deepseek-chat).

Every engine follows the same pattern:
  1. Pull grounded numbers from PostgreSQL (context builders)
  2. Render a prompt template with those numbers embedded as JSON
  3. Call DeepSeek
  4. Parse the CONFIDENCE: 0.XX line out of the response
  5. Return an InsightResult with full provenance (no hidden state)

Engines:
  TrendSummarizationEngine — 3-5 sentence revenue trend narrative
  RootCauseAnalysisEngine  — structured primary-driver / evidence analysis
  ForecastExplanationEngine — business-friendly forecast explanation
  RevenueDriverAnalysisEngine — top-3 driver ranking with % contribution
  ExecutiveSummaryEngine    — board-level 6-section summary
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.insights.context.builders import (
    build_driver_context,
    build_forecast_context,
    build_revenue_context,
    build_signal_context,
    resolve_segment_label,
)
from app.insights.llm.client import get_llm_client, is_llm_configured
from app.insights.prompts.templates import (
    DRIVER_SYSTEM,
    EXECUTIVE_SYSTEM,
    FORECAST_SYSTEM,
    ROOT_CAUSE_SYSTEM,
    TREND_SYSTEM,
    build_driver_prompt,
    build_executive_prompt,
    build_forecast_prompt,
    build_root_cause_prompt,
    build_trend_prompt,
)

log = get_logger(__name__)

_NOT_CONFIGURED_TEXT = (
    "AI insights are not configured. "
    "Set your DEEPSEEK_API_KEY in docker-compose.yml and restart the API container."
)


def _not_configured_result(insight_type: str) -> "InsightResult":
    return InsightResult(
        insight_type=insight_type,
        insight_text=_NOT_CONFIGURED_TEXT,
        confidence=0.0,
        structured_data={},
        model_used="none",
        tokens_total=0,
        latency_ms=0,
    )


# ── Result type ───────────────────────────────────────────

@dataclass
class InsightResult:
    insight_type:    str
    insight_text:    str
    confidence:      float
    structured_data: dict = field(default_factory=dict)
    model_used:      str  = "deepseek-chat"
    tokens_total:    int  = 0
    latency_ms:      int  = 0
    from_cache:      bool = False

    def to_dict(self) -> dict:
        return {
            "insight_type":    self.insight_type,
            "insight_text":    self.insight_text,
            "confidence":      self.confidence,
            "structured_data": self.structured_data,
            "model_used":      self.model_used,
            "tokens_total":    self.tokens_total,
            "latency_ms":      self.latency_ms,
            "from_cache":      self.from_cache,
        }


def _extract_confidence(response_text: str) -> tuple[str, float]:
    """Strip the trailing CONFIDENCE: 0.XX line and return (clean_text, score)."""
    m = re.search(r"\nCONFIDENCE:\s*([\d.]+)\s*$", response_text, re.IGNORECASE)
    if m:
        try:
            score = max(0.0, min(1.0, float(m.group(1))))
            return response_text[: m.start()].strip(), score
        except ValueError:
            pass
    return response_text.strip(), 0.75  # sensible default if model omits the line


# ── 1. Trend Summarization ────────────────────────────────

class TrendSummarizationEngine:
    """3-5 sentence revenue trend narrative covering total, YoY, drivers, pattern."""

    insight_type = "trend"

    def __init__(self, db: Session):
        self._db     = db
        self._client = get_llm_client()

    async def generate(
        self,
        category_id:   Optional[int] = None,
        region_id:     Optional[int] = None,
        lookback_days: int = 90,
    ) -> InsightResult:
        if not is_llm_configured():
            return _not_configured_result(self.insight_type)
        t0  = time.monotonic()
        seg = resolve_segment_label(self._db, category_id, region_id)
        ctx = build_revenue_context(self._db, category_id, region_id, lookback_days)

        user_prompt = build_trend_prompt(seg, ctx)
        resp = await self._client.complete(
            system_prompt=TREND_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.30,
            max_tokens=800,
        )

        clean, confidence = _extract_confidence(resp.text)
        result = InsightResult(
            insight_type=self.insight_type,
            insight_text=clean,
            confidence=confidence,
            structured_data={"segment_key": seg, "revenue_context": ctx},
            model_used=resp.model,
            tokens_total=resp.tokens_total,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        log.info("engine.trend", segment=seg, confidence=confidence)
        return result


# ── 2. Root Cause Analysis ────────────────────────────────

class RootCauseAnalysisEngine:
    """Structured root cause analysis: PRIMARY DRIVER / CONTRIBUTING FACTORS / EVIDENCE / DATA GAPS."""

    insight_type = "root_cause"

    def __init__(self, db: Session):
        self._db     = db
        self._client = get_llm_client()

    async def generate(
        self,
        change_description: str,
        category_id:   Optional[int] = None,
        region_id:     Optional[int] = None,
        lookback_days: int = 60,
    ) -> InsightResult:
        if not is_llm_configured():
            return _not_configured_result(self.insight_type)
        t0   = time.monotonic()
        seg  = resolve_segment_label(self._db, category_id, region_id)
        rev  = build_revenue_context(self._db, category_id, region_id, lookback_days)
        sig  = build_signal_context(self._db, category_id, region_id)

        user_prompt = build_root_cause_prompt(seg, change_description, rev, sig)
        resp = await self._client.complete(
            system_prompt=ROOT_CAUSE_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.25,
            max_tokens=1200,
        )

        clean, confidence = _extract_confidence(resp.text)
        result = InsightResult(
            insight_type=self.insight_type,
            insight_text=clean,
            confidence=confidence,
            structured_data={"segment_key": seg, "revenue_context": rev, "signal_context": sig},
            model_used=resp.model,
            tokens_total=resp.tokens_total,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        log.info("engine.root_cause", segment=seg, confidence=confidence)
        return result


# ── 3. Forecast Explanation ───────────────────────────────

class ForecastExplanationEngine:
    """Business-friendly forecast explanation: total, run-rate, CI bands, MAPE, signal drivers."""

    insight_type = "forecast"

    def __init__(self, db: Session):
        self._db     = db
        self._client = get_llm_client()

    async def generate(
        self,
        category_id:  Optional[int] = None,
        region_id:    Optional[int] = None,
        horizon_days: int = 30,
    ) -> InsightResult:
        if not is_llm_configured():
            return _not_configured_result(self.insight_type)
        t0   = time.monotonic()
        seg  = resolve_segment_label(self._db, category_id, region_id)
        fct  = build_forecast_context(self._db, category_id, region_id, horizon_days)
        rev  = build_revenue_context(self._db, category_id, region_id, 30)
        sig  = build_signal_context(self._db, category_id, region_id)

        user_prompt = build_forecast_prompt(seg, horizon_days, fct, rev, sig)
        resp = await self._client.complete(
            system_prompt=FORECAST_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.25,
            max_tokens=1000,
        )

        clean, confidence = _extract_confidence(resp.text)
        result = InsightResult(
            insight_type=self.insight_type,
            insight_text=clean,
            confidence=confidence,
            structured_data={"segment_key": seg, "forecast_context": fct, "signal_context": sig},
            model_used=resp.model,
            tokens_total=resp.tokens_total,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        log.info("engine.forecast", segment=seg, horizon=horizon_days, confidence=confidence)
        return result


# ── 4. Revenue Driver Analysis ────────────────────────────

class RevenueDriverAnalysisEngine:
    """Ranks the top 3 revenue drivers (category or region) with contribution and trend."""

    insight_type = "driver"

    def __init__(self, db: Session):
        self._db     = db
        self._client = get_llm_client()

    async def generate(
        self,
        category_id:   Optional[int] = None,
        region_id:     Optional[int] = None,
        lookback_days: int = 90,
    ) -> InsightResult:
        if not is_llm_configured():
            return _not_configured_result(self.insight_type)
        t0   = time.monotonic()
        seg  = resolve_segment_label(self._db, category_id, region_id)
        drv  = build_driver_context(self._db, category_id, region_id, lookback_days)
        period_label = f"{drv['period']['start']} to {drv['period']['end']}"

        user_prompt = build_driver_prompt(seg, period_label, drv)
        resp = await self._client.complete(
            system_prompt=DRIVER_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.20,
            max_tokens=1200,
        )

        clean, confidence = _extract_confidence(resp.text)
        result = InsightResult(
            insight_type=self.insight_type,
            insight_text=clean,
            confidence=confidence,
            structured_data={"segment_key": seg, "driver_context": drv},
            model_used=resp.model,
            tokens_total=resp.tokens_total,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        log.info("engine.driver", segment=seg, confidence=confidence)
        return result


# ── 5. Executive Summary ──────────────────────────────────

class ExecutiveSummaryEngine:
    """Board-level summary: headline, performance, forecast, risk, opportunity, action."""

    insight_type = "executive"

    def __init__(self, db: Session):
        self._db     = db
        self._client = get_llm_client()

    async def generate(
        self,
        category_id:   Optional[int] = None,
        region_id:     Optional[int] = None,
        lookback_days: int = 90,
        horizon_days:  int = 30,
    ) -> InsightResult:
        if not is_llm_configured():
            return _not_configured_result(self.insight_type)
        t0   = time.monotonic()
        seg  = resolve_segment_label(self._db, category_id, region_id)
        rev  = build_revenue_context(self._db, category_id, region_id, lookback_days)
        fct  = build_forecast_context(self._db, category_id, region_id, horizon_days)
        sig  = build_signal_context(self._db, category_id, region_id)
        period_label = f"{rev['period']['start']} to {rev['period']['end']}"

        user_prompt = build_executive_prompt(seg, period_label, rev, fct, sig)
        resp = await self._client.complete(
            system_prompt=EXECUTIVE_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.30,
            max_tokens=1500,
        )

        clean, confidence = _extract_confidence(resp.text)
        result = InsightResult(
            insight_type=self.insight_type,
            insight_text=clean,
            confidence=confidence,
            structured_data={"segment_key": seg, "revenue_context": rev, "forecast_context": fct, "signal_context": sig},
            model_used=resp.model,
            tokens_total=resp.tokens_total,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        log.info("engine.executive", segment=seg, confidence=confidence)
        return result
