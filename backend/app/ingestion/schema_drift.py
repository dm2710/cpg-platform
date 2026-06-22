"""
Schema drift handler.

Resolves arbitrary source field names to the canonical set:
  transaction_date, sku_id, category_name, region_name,
  store_id, revenue, quantity, currency, unit, record_id

Resolution priority:
  1. Already canonical
  2. DB alias (field_aliases table, cached per source)
  3. Hardcoded fallback alias map
  4. Fuzzy match against canonical names (cutoff 0.72)
  5. Unknown — field logged and dropped
"""

from difflib import get_close_matches
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.schemas.base import IssueType
from app.validation.engine import ValidationResult

log = get_logger(__name__)

CANONICAL_FIELDS = {
    "transaction_date", "sku_id", "category_name", "region_name",
    "store_id", "revenue", "quantity", "currency", "unit", "record_id",
    "source_name",
}

REQUIRED_CANONICAL = {"transaction_date", "revenue"}

FALLBACK_ALIASES: dict[str, str] = {
    # date
    "date": "transaction_date", "sale_date": "transaction_date",
    "order_date": "transaction_date", "created_at": "transaction_date",
    "closed_date": "transaction_date", "close_date": "transaction_date",
    "invoice_date": "transaction_date", "txn_date": "transaction_date",
    "transaction_datetime": "transaction_date",
    # revenue
    "amount": "revenue", "total": "revenue", "total_price": "revenue",
    "net_amount": "revenue", "gross_amount": "revenue", "sales": "revenue",
    "revenue_usd": "revenue", "amount_usd": "revenue", "price": "revenue",
    "sale_amount": "revenue", "order_total": "revenue", "value": "revenue",
    "net_sales": "revenue", "gross_sales": "revenue",
    # category
    "category": "category_name", "product_type": "category_name",
    "dept": "category_name", "department": "category_name",
    "product_family": "category_name", "product_category": "category_name",
    "segment": "category_name", "vertical": "category_name",
    "item_category": "category_name",
    # region
    "region": "region_name", "territory": "region_name",
    "market": "region_name", "country": "region_name",
    "store_region": "region_name", "shipping_country": "region_name",
    "geo": "region_name", "geography": "region_name", "area": "region_name",
    # sku
    "sku": "sku_id", "product_id": "sku_id", "item_code": "sku_id",
    "article_no": "sku_id", "product_code": "sku_id", "upc": "sku_id",
    "barcode": "sku_id", "gtin": "sku_id",
    # store
    "store": "store_id", "location": "store_id", "location_id": "store_id",
    "outlet": "store_id", "branch": "store_id",
    # quantity
    "qty": "quantity", "units": "quantity", "count": "quantity",
    "quantity_sold": "quantity", "num_units": "quantity", "pieces": "quantity",
    # currency
    "currency_code": "currency", "ccy": "currency", "curr": "currency",
    # unit
    "unit_of_measure": "unit", "uom": "unit",
    # record id
    "id": "record_id", "order_id": "record_id", "transaction_ref": "record_id",
    "ref": "record_id", "pos_id": "record_id",
}

# In-process cache: source_name → {source_field: canonical_field}
_alias_cache: dict[str, dict[str, str]] = {}


def _normalise_key(key: str) -> str:
    return key.strip().lower().replace(" ", "_").replace("-", "_").replace(".", "_")


def load_db_aliases(source_name: str, db: Session) -> dict[str, str]:
    if source_name in _alias_cache:
        return _alias_cache[source_name]

    rows = db.execute(
        text("SELECT source_field, canonical_field FROM field_aliases WHERE source_name = :s"),
        {"s": source_name},
    ).fetchall()
    mapping = {r[0].lower(): r[1] for r in rows}
    _alias_cache[source_name] = mapping
    return mapping


def clear_alias_cache() -> None:
    _alias_cache.clear()


def resolve_record(
    raw: dict,
    source_name: str,
    db: Session,
) -> tuple[dict, list[dict]]:
    """
    Map a raw source record to canonical fields.
    Returns (canonical_record, list_of_dq_issues).
    """
    db_aliases = load_db_aliases(source_name, db)
    issues: list[dict] = []
    resolved: dict = {}

    for raw_key, raw_val in raw.items():
        key = _normalise_key(raw_key)

        # 1. Already canonical
        if key in CANONICAL_FIELDS:
            resolved[key] = raw_val
            continue

        # 2. DB alias
        if key in db_aliases:
            canonical = db_aliases[key]
            resolved[canonical] = raw_val
            issues.append(_issue(IssueType.SCHEMA_DRIFT, source_name,
                f"'{raw_key}' → '{canonical}' via DB alias",
                raw_key, canonical, auto_corrected=True))
            continue

        # 3. Hardcoded fallback
        if key in FALLBACK_ALIASES:
            canonical = FALLBACK_ALIASES[key]
            resolved[canonical] = raw_val
            issues.append(_issue(IssueType.SCHEMA_DRIFT, source_name,
                f"'{raw_key}' → '{canonical}' via fallback alias",
                raw_key, canonical, auto_corrected=True))
            continue

        # 4. Fuzzy match
        matches = get_close_matches(key, CANONICAL_FIELDS, n=1, cutoff=0.72)
        if matches:
            canonical = matches[0]
            resolved[canonical] = raw_val
            issues.append(_issue(IssueType.SCHEMA_DRIFT, source_name,
                f"'{raw_key}' fuzzy-matched to '{canonical}' (score ≥ 0.72)",
                raw_key, canonical, severity="warning", auto_corrected=True))
            continue

        # 5. Unknown — drop
        issues.append(_issue(IssueType.SCHEMA_DRIFT, source_name,
            f"Unknown field '{raw_key}' — dropped",
            raw_key, None, severity="info"))

    return resolved, issues


def resolve_batch(
    records: list[dict],
    source_name: str,
    db: Session,
) -> tuple[list[dict], list[dict]]:
    """Resolve a batch of raw records. Returns (resolved_records, all_issues)."""
    all_resolved = []
    all_issues: list[dict] = []

    for record in records:
        resolved, issues = resolve_record(record, source_name, db)
        all_resolved.append(resolved)
        all_issues.extend(issues)

    return all_resolved, all_issues


def _issue(
    issue_type: IssueType,
    source_name: str,
    detail: str,
    raw_value: Optional[str],
    corrected_value: Optional[str],
    severity: str = "info",
    auto_corrected: bool = False,
) -> dict:
    return {
        "issue_type":      issue_type.value,
        "source_name":     source_name,
        "issue_detail":    detail,
        "raw_value":       raw_value,
        "corrected_value": corrected_value,
        "severity":        severity,
        "auto_corrected":  auto_corrected,
    }
