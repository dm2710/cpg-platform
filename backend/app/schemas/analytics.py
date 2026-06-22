"""
Pydantic schemas for analytics query responses and DQ monitoring.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from app.schemas.base import CamelBase


# ── Analytics ─────────────────────────────────────────────

class RevenuePeriod(CamelBase):
    period:      date
    revenue:     Decimal
    quantity:    int
    txn_count:   int


class RevenueBreakdownItem(CamelBase):
    label:    str
    revenue:  Decimal
    quantity: int
    pct:      Optional[float] = None    # % of total, computed by endpoint


class SummaryKpi(CamelBase):
    total_revenue:  Decimal
    total_quantity: int
    earliest_date:  Optional[date]
    latest_date:    Optional[date]
    category_count: int
    region_count:   int


class CategoryOut(CamelBase):
    category_id:   int
    category_name: str


class RegionOut(CamelBase):
    region_id:   int
    region_name: str


# ── Data quality ──────────────────────────────────────────

class DqIssueSummaryRow(CamelBase):
    source_name:          str
    issue_type:           str
    severity:             str
    issue_count:          int
    auto_corrected_count: int
    last_seen:            datetime


class DqIssueRow(CamelBase):
    id:              int
    source_name:     str
    issue_type:      str
    issue_detail:    Optional[str]
    raw_value:       Optional[str]
    corrected_value: Optional[str]
    severity:        str
    auto_corrected:  bool
    detected_at:     datetime


class LateArrivalRow(CamelBase):
    id:               int
    transaction_date: date
    ingested_at:      datetime
    lateness_days:    int
    severity:         str
    source_name:      str
    resolved:         bool


class SourceHealthRow(CamelBase):
    source_name:     str
    total_staged:    int
    processed:       int
    last_ingested_at: Optional[datetime]
    total_issues:    int
    error_count:     int
    warning_count:   int


class RecomputeResponse(CamelBase):
    recomputed:      bool
    from_date:       Optional[str]
    affected_dates:  list[str]
