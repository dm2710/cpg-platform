"""
Pydantic schemas for all ingestion endpoints.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from pydantic import Field, field_validator, model_validator

from app.schemas.base import CamelBase, DqIssueEmbed, IssueType


# ── Inbound transaction record ────────────────────────────

class TransactionRecordIn(CamelBase):
    """
    Flexible input schema — accepts any field name combination.
    The ingestion pipeline resolves arbitrary names to canonical fields
    via schema drift handlers, so we accept extra fields here.
    """
    model_config = {"extra": "allow", "populate_by_name": True}

    # canonical names (sources that already speak our schema)
    transaction_date: Optional[str]     = None
    sku_id:           Optional[str]     = None
    category_name:    Optional[str]     = None
    region_name:      Optional[str]     = None
    store_id:         Optional[str]     = None
    revenue:          Optional[float]   = None
    quantity:         Optional[int]     = None
    currency:         Optional[str]     = "USD"
    unit:             Optional[str]     = None
    source_name:      Optional[str]     = "api_push"
    record_id:        Optional[str]     = None   # source-system PK for dedup


class PushPayload(CamelBase):
    """JSON push from any source (POS webhook, e-comm event, CRM sync)."""
    records:     list[dict[str, Any]] = Field(..., min_length=1, max_length=5000)
    source_name: str                  = Field(default="api_push", max_length=80)


# ── Pipeline result ───────────────────────────────────────

class PipelineResult(CamelBase):
    total_received:     int
    accepted:           int
    duplicates_skipped: int
    rejected:           int
    late_flagged:       int
    recompute_triggered: bool
    issue_summary:      dict[str, int] = Field(default_factory=dict)
    errors:             list[str]      = Field(default_factory=list)


class CsvUploadResponse(CamelBase):
    source_name:    str
    filename:       str
    rows_detected:  int
    pipeline_result: PipelineResult


class PushResponse(CamelBase):
    sources_processed: int
    pipeline_result:   PipelineResult


# ── Staging query response ────────────────────────────────

class StagingRecordOut(CamelBase):
    staging_id:       int
    source_name:      str
    transaction_date: Optional[date]
    category_name:    Optional[str]
    region_name:      Optional[str]
    revenue:          Optional[Decimal]
    quantity:         Optional[int]
    currency:         str
    processed:        bool
    ingested_at:      str
    error_message:    Optional[str]
