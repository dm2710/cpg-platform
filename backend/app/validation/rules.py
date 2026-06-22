"""
Concrete validation rules for CPG transaction records.

Each rule targets one concern. Rules are assembled into a RuleEngine
by the pipeline orchestrator — different source types may use
different rule sets (e.g. POS rules differ from CRM rules).
"""

import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from app.schemas.base import IssueType
from app.validation.engine import RuleOutcome, ValidationResult, ValidationRule

# ── Date parsing helpers ──────────────────────────────────

DATE_FORMATS = [
    "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y",
    "%Y/%m/%d", "%m-%d-%Y", "%d.%m.%Y", "%Y.%m.%d",
    "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
]


def _parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    s = str(value).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        try:
            return datetime.fromisoformat(s[:10]).date()
        except ValueError:
            pass
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^\d.\-]", "", str(value))
    try:
        return float(cleaned)
    except ValueError:
        return None


# ── Rule 1: Required fields ───────────────────────────────

class RequiredFieldsRule(ValidationRule):
    """Reject records missing any required canonical field."""
    name = "required_fields"

    REQUIRED = {"transaction_date", "revenue"}
    WARN_MISSING = {"category_name", "region_name"}

    def validate(self, record: dict, context: Optional[dict] = None) -> ValidationResult:
        for field in self.REQUIRED:
            if record.get(field) is None or str(record.get(field, "")).strip() == "":
                return ValidationResult.reject(
                    issue_type=IssueType.MISSING_REQUIRED,
                    detail=f"Required field '{field}' is missing or empty",
                    field_name=field,
                )
        for field in self.WARN_MISSING:
            if record.get(field) is None:
                return ValidationResult.warn(
                    issue_type=IssueType.MISSING_REQUIRED,
                    detail=f"Recommended field '{field}' is missing — will use 'Unknown'",
                    field_name=field,
                    corrected_record={**record, field: "Unknown"},
                )
        return ValidationResult.ok()


# ── Rule 2: Date parsing and validity ─────────────────────

class DateValidationRule(ValidationRule):
    """Parse transaction_date to a Python date; reject if unparseable."""
    name = "date_validation"

    def validate(self, record: dict, context: Optional[dict] = None) -> ValidationResult:
        raw = record.get("transaction_date")
        parsed = _parse_date(raw)

        if parsed is None:
            return ValidationResult.reject(
                issue_type=IssueType.UNPARSEABLE_DATE,
                detail=f"Cannot parse '{raw}' as a date (tried {len(DATE_FORMATS)} formats)",
                raw_value=str(raw),
                field_name="transaction_date",
            )

        today = date.today()
        if parsed > today:
            return ValidationResult.warn(
                issue_type=IssueType.FUTURE_DATE,
                detail=f"transaction_date {parsed} is in the future — clamped to today",
                raw_value=str(raw),
                corrected_value=str(today),
                field_name="transaction_date",
                corrected_record={**record, "transaction_date": today},
            )

        # Date too far in the past (>10 years) — warn but accept
        age_days = (today - parsed).days
        if age_days > 3650:
            return ValidationResult.warn(
                issue_type=IssueType.BUSINESS_RULE,
                detail=f"transaction_date {parsed} is {age_days} days old (>10 years)",
                raw_value=str(raw),
                field_name="transaction_date",
                corrected_record={**record, "transaction_date": parsed},
            )

        return ValidationResult.ok() if parsed == record.get("transaction_date") else ValidationResult.warn(
            issue_type=IssueType.TYPE_COERCION,
            detail=f"Date coerced from '{raw}' to {parsed}",
            raw_value=str(raw),
            corrected_value=str(parsed),
            field_name="transaction_date",
            corrected_record={**record, "transaction_date": parsed},
        )


# ── Rule 3: Revenue coercion and sign ─────────────────────

class RevenueValidationRule(ValidationRule):
    """Parse revenue to float; reject if unparseable; warn if negative."""
    name = "revenue_validation"

    def validate(self, record: dict, context: Optional[dict] = None) -> ValidationResult:
        raw = record.get("revenue")
        value = _parse_float(raw)

        if value is None:
            return ValidationResult.reject(
                issue_type=IssueType.TYPE_COERCION,
                detail=f"Cannot parse revenue '{raw}' as a number",
                raw_value=str(raw),
                field_name="revenue",
            )

        if value < 0:
            return ValidationResult.warn(
                issue_type=IssueType.NEGATIVE_REVENUE,
                detail=f"Negative revenue {value} — likely a return/refund; kept as-is",
                raw_value=str(raw),
                corrected_value=str(value),
                field_name="revenue",
                corrected_record={**record, "revenue": value},
            )

        if value == 0:
            return ValidationResult.warn(
                issue_type=IssueType.BUSINESS_RULE,
                detail="Zero revenue record — may be a sample, freebie, or error",
                raw_value=str(raw),
                field_name="revenue",
                corrected_record={**record, "revenue": 0.0},
            )

        corrected = {**record, "revenue": value}
        if value != raw:
            return ValidationResult.warn(
                issue_type=IssueType.TYPE_COERCION,
                detail=f"Revenue coerced from '{raw}' to {value}",
                raw_value=str(raw),
                corrected_value=str(value),
                field_name="revenue",
                corrected_record=corrected,
            )

        return ValidationResult.ok()


# ── Rule 4: Quantity coercion ─────────────────────────────

class QuantityValidationRule(ValidationRule):
    """Coerce quantity to int; default to 1 if missing; warn if zero."""
    name = "quantity_validation"

    def validate(self, record: dict, context: Optional[dict] = None) -> ValidationResult:
        raw = record.get("quantity")

        if raw is None:
            return ValidationResult.warn(
                issue_type=IssueType.MISSING_REQUIRED,
                detail="quantity missing — defaulting to 1",
                corrected_value="1",
                field_name="quantity",
                corrected_record={**record, "quantity": 1},
            )

        try:
            value = int(float(str(raw).replace(",", "").strip()))
        except (ValueError, TypeError):
            return ValidationResult.warn(
                issue_type=IssueType.TYPE_COERCION,
                detail=f"Cannot parse quantity '{raw}' as integer — defaulting to 1",
                raw_value=str(raw),
                corrected_value="1",
                field_name="quantity",
                corrected_record={**record, "quantity": 1},
            )

        if value <= 0:
            return ValidationResult.warn(
                issue_type=IssueType.ZERO_QUANTITY,
                detail=f"Non-positive quantity {value} — set to 1",
                raw_value=str(raw),
                corrected_value="1",
                field_name="quantity",
                corrected_record={**record, "quantity": 1},
            )

        return ValidationResult.ok() if value == raw else ValidationResult.warn(
            issue_type=IssueType.TYPE_COERCION,
            detail=f"Quantity coerced from '{raw}' to {value}",
            raw_value=str(raw),
            corrected_value=str(value),
            corrected_record={**record, "quantity": value},
        )


# ── Rule 5: Currency normalisation ────────────────────────

class CurrencyValidationRule(ValidationRule):
    """Normalise currency to 3-letter uppercase; default to USD."""
    name = "currency_validation"

    KNOWN_CURRENCIES = {
        "USD", "EUR", "GBP", "JPY", "INR", "AUD", "CAD",
        "SGD", "AED", "BRL", "MXN", "CNY", "CHF", "KRW",
    }

    def validate(self, record: dict, context: Optional[dict] = None) -> ValidationResult:
        raw = record.get("currency", "USD")
        if raw is None:
            return ValidationResult.warn(
                issue_type=IssueType.TYPE_COERCION,
                detail="Currency missing — defaulting to USD",
                corrected_value="USD",
                corrected_record={**record, "currency": "USD"},
            )

        normalised = str(raw).strip().upper()[:3]

        if normalised not in self.KNOWN_CURRENCIES:
            return ValidationResult.warn(
                issue_type=IssueType.UNKNOWN_CURRENCY,
                detail=f"Unknown currency '{raw}' — will attempt FX lookup; fallback to USD",
                raw_value=str(raw),
                corrected_value=normalised,
                corrected_record={**record, "currency": normalised},
            )

        if normalised != raw:
            return ValidationResult.warn(
                issue_type=IssueType.TYPE_COERCION,
                detail=f"Currency normalised from '{raw}' to '{normalised}'",
                raw_value=str(raw),
                corrected_value=normalised,
                corrected_record={**record, "currency": normalised},
            )

        return ValidationResult.ok()


# ── Rule 6: String field sanitisation ────────────────────

class StringSanitisationRule(ValidationRule):
    """Strip whitespace and title-case string dimension fields."""
    name = "string_sanitisation"

    STRING_FIELDS = ["category_name", "region_name", "store_id", "sku_id", "brand"]

    def validate(self, record: dict, context: Optional[dict] = None) -> ValidationResult:
        corrected = dict(record)
        changed = False

        for field in self.STRING_FIELDS:
            val = record.get(field)
            if val is None:
                continue
            cleaned = str(val).strip()
            if field in ("category_name", "region_name"):
                cleaned = cleaned.title()
            if cleaned != val:
                corrected[field] = cleaned
                changed = True

        if changed:
            return ValidationResult.warn(
                issue_type=IssueType.TYPE_COERCION,
                detail="String fields stripped/normalised",
                auto_corrected=True,
                corrected_record=corrected,
            )
        return ValidationResult.ok()


# ── Rule 7: Revenue / quantity ratio plausibility ─────────

class PlausibilityRule(ValidationRule):
    """
    Warn if unit price (revenue / quantity) is implausibly high or low.
    Thresholds are CPG-typical; adjust per business.
    """
    name = "plausibility"

    MIN_UNIT_PRICE = 0.01    # USD
    MAX_UNIT_PRICE = 50_000  # USD — single CPG unit

    def validate(self, record: dict, context: Optional[dict] = None) -> ValidationResult:
        revenue  = record.get("revenue")
        quantity = record.get("quantity")

        if not isinstance(revenue, (int, float)) or not isinstance(quantity, int):
            return ValidationResult.ok()

        if quantity <= 0:
            return ValidationResult.ok()  # handled by QuantityValidationRule

        unit_price = revenue / quantity

        if unit_price > self.MAX_UNIT_PRICE:
            return ValidationResult.warn(
                issue_type=IssueType.BUSINESS_RULE,
                detail=f"Unit price ${unit_price:,.2f} exceeds ${self.MAX_UNIT_PRICE:,} threshold — verify",
                raw_value=f"revenue={revenue}, quantity={quantity}",
                field_name="revenue",
            )

        if 0 < unit_price < self.MIN_UNIT_PRICE:
            return ValidationResult.warn(
                issue_type=IssueType.BUSINESS_RULE,
                detail=f"Unit price ${unit_price:.4f} is below ${self.MIN_UNIT_PRICE} minimum — verify",
                raw_value=f"revenue={revenue}, quantity={quantity}",
                field_name="revenue",
            )

        return ValidationResult.ok()


# ── Rule 8: SKU format check ──────────────────────────────

class SkuFormatRule(ValidationRule):
    """Warn if sku_id contains suspicious characters or is suspiciously long."""
    name = "sku_format"

    MAX_LENGTH = 80

    def validate(self, record: dict, context: Optional[dict] = None) -> ValidationResult:
        sku = record.get("sku_id")
        if sku is None:
            return ValidationResult.ok()  # SKU is optional

        sku_str = str(sku).strip()
        if len(sku_str) > self.MAX_LENGTH:
            truncated = sku_str[:self.MAX_LENGTH]
            return ValidationResult.warn(
                issue_type=IssueType.TYPE_COERCION,
                detail=f"sku_id truncated from {len(sku_str)} to {self.MAX_LENGTH} chars",
                raw_value=sku_str,
                corrected_value=truncated,
                corrected_record={**record, "sku_id": truncated},
            )

        if re.search(r"[<>\"';\\]", sku_str):
            return ValidationResult.warn(
                issue_type=IssueType.BUSINESS_RULE,
                detail=f"sku_id '{sku_str}' contains suspicious characters",
                raw_value=sku_str,
                field_name="sku_id",
            )

        return ValidationResult.ok()


# ── Pre-built rule sets ───────────────────────────────────

def get_standard_rules() -> list[ValidationRule]:
    """Full rule set for CSV / API push ingestion."""
    return [
        RequiredFieldsRule(),
        DateValidationRule(),
        RevenueValidationRule(),
        QuantityValidationRule(),
        CurrencyValidationRule(),
        StringSanitisationRule(),
        PlausibilityRule(),
        SkuFormatRule(),
    ]


def get_minimal_rules() -> list[ValidationRule]:
    """Lightweight set for pre-validated sources."""
    return [
        RequiredFieldsRule(),
        DateValidationRule(),
        RevenueValidationRule(),
    ]
