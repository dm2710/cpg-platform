"""
Shared Pydantic base classes, enums, and response wrappers.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


# ── Shared enums ──────────────────────────────────────────

class SourceType(str, Enum):
    POS        = "pos"
    ECOMMERCE  = "ecommerce"
    CRM        = "crm"
    MANUAL     = "manual"
    API        = "api"
    TEST       = "test"


class IssueSeverity(str, Enum):
    INFO    = "info"
    WARNING = "warning"
    ERROR   = "error"


class IssueType(str, Enum):
    SCHEMA_DRIFT          = "schema_drift"
    UNKNOWN_FIELD         = "unknown_field"
    MISSING_REQUIRED      = "missing_required_field"
    TYPE_COERCION         = "type_coercion"
    UNPARSEABLE_DATE      = "unparseable_date"
    DUPLICATE             = "duplicate"
    LATE_ARRIVAL          = "late_arrival"
    VERY_LATE_ARRIVAL     = "very_late_arrival"
    FX_RATE_FALLBACK      = "fx_rate_fallback"
    UNKNOWN_CURRENCY      = "unknown_currency"
    UNIT_CONVERSION       = "unit_conversion"
    UNKNOWN_UNIT          = "unknown_unit"
    BUSINESS_RULE         = "business_rule"
    NEGATIVE_REVENUE      = "negative_revenue"
    ZERO_QUANTITY         = "zero_quantity"
    FUTURE_DATE           = "future_date"


class LatenessSeverity(str, Enum):
    NORMAL     = "normal"
    SOFT_LATE  = "soft_late"
    LATE       = "late"
    VERY_LATE  = "very_late"


class Granularity(str, Enum):
    DAY   = "day"
    WEEK  = "week"
    MONTH = "month"


class BreakdownDimension(str, Enum):
    CATEGORY = "category"
    REGION   = "region"
    STORE    = "store"
    SOURCE   = "source"


# ── Base config ───────────────────────────────────────────

def _to_camel(snake_str: str) -> str:
    parts = snake_str.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


class CamelBase(BaseModel):
    """All API schemas use camelCase for JSON, snake_case internally."""
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        use_enum_values=True,
        from_attributes=True,
    )


# ── Generic response wrappers ─────────────────────────────

class DataResponse(CamelBase, Generic[T]):
    data: T
    meta: Optional[dict[str, Any]] = None


class PaginatedResponse(CamelBase, Generic[T]):
    data: list[T]
    total: int
    page: int
    page_size: int
    pages: int


class MessageResponse(CamelBase):
    message: str
    success: bool = True


# ── DQ issue embedded schema ──────────────────────────────

class DqIssueEmbed(CamelBase):
    issue_type:      str
    issue_detail:    Optional[str] = None
    raw_value:       Optional[str] = None
    corrected_value: Optional[str] = None
    severity:        IssueSeverity = IssueSeverity.WARNING
    auto_corrected:  bool          = False
