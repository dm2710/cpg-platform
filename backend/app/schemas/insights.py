"""Phase 3 — AI Insights Pydantic schemas."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import Field

from app.schemas.base import CamelBase


class InsightBase(CamelBase):
    insight_type:    str
    insight_text:    str
    confidence:      float          = Field(ge=0.0, le=1.0)
    structured_data: dict[str, Any] = Field(default_factory=dict)
    model_used:      str            = "deepseek-chat"
    tokens_total:    int            = 0
    latency_ms:      int            = 0
    from_cache:      bool           = False


# ── 1. Trend Summarization ────────────────────────────────

class TrendRequest(CamelBase):
    category_id:   Optional[int] = None
    region_id:     Optional[int] = None
    lookback_days: int = Field(default=90, ge=7, le=365)

class TrendResponse(InsightBase):
    pass


# ── 2. Root Cause Analysis ────────────────────────────────

class RootCauseRequest(CamelBase):
    change_description: str           = Field(..., min_length=10, max_length=500)
    category_id:        Optional[int] = None
    region_id:           Optional[int] = None
    lookback_days:       int           = Field(default=60, ge=7, le=180)

class RootCauseResponse(InsightBase):
    pass


# ── 3. Forecast Explanation ───────────────────────────────

class ForecastExplainRequest(CamelBase):
    category_id:  Optional[int] = None
    region_id:    Optional[int] = None
    horizon_days: int           = Field(default=30, ge=7, le=365)

class ForecastExplainResponse(InsightBase):
    pass


# ── 4. Revenue Driver Analysis ────────────────────────────

class DriverRequest(CamelBase):
    category_id:   Optional[int] = None
    region_id:     Optional[int] = None
    lookback_days: int           = Field(default=90, ge=14, le=365)

class DriverResponse(InsightBase):
    pass


# ── 5. Executive Summary ──────────────────────────────────

class ExecutiveRequest(CamelBase):
    category_id:   Optional[int] = None
    region_id:     Optional[int] = None
    lookback_days: int           = Field(default=90, ge=14, le=365)
    horizon_days:  int           = Field(default=30, ge=7,  le=365)

class ExecutiveResponse(InsightBase):
    pass


# ── Audit log ─────────────────────────────────────────────

class InsightLogOut(CamelBase):
    log_id:       int
    insight_type: str
    segment_key:  Optional[str]
    question:     Optional[str]
    confidence:   Optional[Decimal]
    model_used:   Optional[str]
    tokens_total: Optional[int]
    latency_ms:   Optional[int]
    from_cache:   bool
    status:       str
    requested_at: datetime


# ── Conversational analytics (Phase 4) ────────────────────

class SessionCreateRequest(CamelBase):
    category_id: Optional[int] = None
    region_id:   Optional[int] = None
    title:       Optional[str] = None

class SessionCreateResponse(CamelBase):
    session_id: str
    title:      str

class SessionOut(CamelBase):
    session_id:     str
    title:          Optional[str]
    segment_key:    Optional[str]
    created_at:     datetime
    last_active_at: datetime
    message_count:  int
    is_active:      bool

class MessageOut(CamelBase):
    role:       str
    content:    str
    confidence: Optional[float]
    created_at: datetime

class AskRequest(CamelBase):
    session_id:  str
    question:    str           = Field(..., min_length=3, max_length=1000)
    category_id: Optional[int] = None
    region_id:   Optional[int] = None

class AskResponse(CamelBase):
    session_id:   str
    question:     str
    answer:       str
    confidence:   float
    model_used:   str
    tokens_total: int
    latency_ms:   int
