"""
Source connectors — one connector per upstream system.

Each connector is responsible for:
  1. Accepting raw source payload (bytes or dict list)
  2. Applying source-specific pre-processing (date format hints,
     known field mappings, filtering junk rows)
  3. Returning a list of raw dicts for the main pipeline

Connectors do NOT validate or normalise — that's the pipeline's job.
They only handle source-specific structural quirks.
"""

from datetime import date
from typing import Any, Optional

from app.core.logging import get_logger
from app.ingestion.parsers.csv_parser import parse_csv

log = get_logger(__name__)


# ── Base connector ────────────────────────────────────────

class BaseConnector:
    source_name: str = "unknown"
    source_type: str = "unknown"

    def parse(self, payload: Any) -> list[dict]:
        raise NotImplementedError

    def pre_process(self, records: list[dict]) -> list[dict]:
        """Override in subclasses for source-specific cleanup."""
        return records

    def run(self, payload: Any) -> list[dict]:
        records = self.parse(payload)
        records = self.pre_process(records)
        log.info(
            "connector.run",
            source=self.source_name,
            records=len(records),
        )
        return records


# ── CSV upload connector (manual / ad-hoc files) ──────────

class CsvConnector(BaseConnector):
    source_type = "manual"

    def __init__(self, source_name: str = "csv_upload"):
        self.source_name = source_name

    def parse(self, payload: bytes) -> list[dict]:
        return parse_csv(payload, self.source_name)

    def pre_process(self, records: list[dict]) -> list[dict]:
        # Drop rows where every meaningful field is empty
        return [
            r for r in records
            if any(v is not None for k, v in r.items())
        ]


# ── Generic JSON push (API webhook / real-time events) ────

class JsonPushConnector(BaseConnector):
    source_type = "api"

    def __init__(self, source_name: str = "api_push"):
        self.source_name = source_name

    def parse(self, payload: list[dict]) -> list[dict]:
        if not isinstance(payload, list):
            raise ValueError(f"JSON push expects a list of records, got {type(payload)}")
        return payload

    def pre_process(self, records: list[dict]) -> list[dict]:
        cleaned = []
        for rec in records:
            if not isinstance(rec, dict):
                log.warning("json_push.bad_record", type=type(rec).__name__)
                continue
            # Attach source_name if not already present
            rec.setdefault("source_name", self.source_name)
            cleaned.append(rec)
        return cleaned


# ── POS legacy connector ──────────────────────────────────

class PosLegacyConnector(BaseConnector):
    """
    Handles legacy POS flat-file exports.
    These often have fixed-width or semicolon-delimited formats
    with non-standard column names and local date formats.
    """
    source_name = "pos_legacy"
    source_type = "pos"

    def parse(self, payload: bytes) -> list[dict]:
        return parse_csv(payload, self.source_name)

    def pre_process(self, records: list[dict]) -> list[dict]:
        cleaned = []
        for rec in records:
            # POS exports often include summary/header rows — skip them
            if rec.get("sale_date", "").lower() in ("sale_date", "date", "total", ""):
                continue
            # Remove POS-specific metadata columns we don't need
            for drop_col in ("cashier_id", "terminal_id", "void_flag", "tax_amount"):
                rec.pop(drop_col, None)
            cleaned.append(rec)
        return cleaned


# ── Shopify connector ─────────────────────────────────────

class ShopifyConnector(BaseConnector):
    """
    Handles Shopify order exports (CSV from admin or API webhook payload).

    Shopify field names differ significantly from our canonical schema;
    the field_aliases table covers most, but we add a few structural fixes here:
      - Splits "Name" (order reference) into record_id
      - Handles "Lineitem quantity" / "Lineitem price" compound rows
      - Strips Shopify's default "#" prefix from order numbers
    """
    source_name = "shopify"
    source_type = "ecommerce"

    def parse(self, payload: bytes) -> list[dict]:
        return parse_csv(payload, self.source_name)

    def pre_process(self, records: list[dict]) -> list[dict]:
        cleaned = []
        for rec in records:
            # Extract order reference as record_id for dedup
            order_ref = rec.pop("name", None) or rec.pop("order_name", None)
            if order_ref:
                rec["record_id"] = str(order_ref).lstrip("#").strip()

            # Shopify uses "lineitem_price" + "lineitem_quantity" for line items
            if "lineitem_price" in rec and "lineitem_quantity" in rec:
                rec.setdefault("revenue", rec.pop("lineitem_price"))
                rec.setdefault("quantity", rec.pop("lineitem_quantity"))

            # Skip cancelled/refund-only rows with no revenue
            financial_status = rec.get("financial_status", "").lower()
            if financial_status in ("voided", "refunded") and not rec.get("revenue"):
                continue

            # Shopify timestamps include timezone — keep only the date part
            for ts_field in ("created_at", "processed_at"):
                if ts_field in rec and rec[ts_field]:
                    rec[ts_field] = str(rec[ts_field])[:10]

            cleaned.append(rec)
        return cleaned


# ── CRM export connector ──────────────────────────────────

class CrmExportConnector(BaseConnector):
    """
    Handles CRM opportunity exports (Salesforce, HubSpot, etc.).
    CRM data maps closed deals → revenue; open/lost deals are filtered.
    """
    source_name = "crm_export"
    source_type = "crm"

    CLOSED_WON_STATUSES = {
        "closed won", "closedwon", "won", "closed - won",
        "won deal", "deal won", "contracted",
    }

    def parse(self, payload: bytes) -> list[dict]:
        return parse_csv(payload, self.source_name)

    def pre_process(self, records: list[dict]) -> list[dict]:
        cleaned = []
        for rec in records:
            # Only include closed-won opportunities
            stage = (
                rec.get("stage", "") or
                rec.get("opportunity_stage", "") or
                rec.get("deal_stage", "")
            ).lower().strip()

            if stage and stage not in self.CLOSED_WON_STATUSES:
                continue

            # Use opportunity ID as record_id for stable dedup
            opp_id = (
                rec.get("opportunity_id") or
                rec.get("deal_id") or
                rec.get("id")
            )
            if opp_id:
                rec["record_id"] = str(opp_id)

            cleaned.append(rec)

        log.info(
            "crm.filtered",
            source=self.source_name,
            raw=len(records),
            kept=len(cleaned),
        )
        return cleaned


# ── Connector registry ────────────────────────────────────

_REGISTRY: dict[str, type[BaseConnector]] = {
    "csv_upload":  CsvConnector,
    "api_push":    JsonPushConnector,
    "pos_legacy":  PosLegacyConnector,
    "shopify":     ShopifyConnector,
    "crm_export":  CrmExportConnector,
    "synthetic":   JsonPushConnector,
}


def get_connector(source_name: str) -> BaseConnector:
    """Return the appropriate connector for a source name."""
    cls = _REGISTRY.get(source_name, JsonPushConnector)
    return cls(source_name) if cls is JsonPushConnector else cls()
