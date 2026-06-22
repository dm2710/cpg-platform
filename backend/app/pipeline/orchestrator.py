"""
Ingestion pipeline orchestrator.

Runs every inbound record through six sequential stages:

  Stage 1 — Schema drift    : resolve field names → canonical
  Stage 2 — Validation      : type coercion, business rules, DQ
  Stage 3 — Deduplication   : fingerprint-based idempotency
  Stage 4 — Normalisation   : FX → USD, units → canonical
  Stage 5 — Late arrival    : classify lateness, build issues
  Stage 6 — Persist         : staging insert → fingerprint register
                              → fact load → aggregate refresh

Returns a PipelineRunResult with full accounting of what happened.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.ingestion import deduplication, late_arrivals, normalization, schema_drift
from app.validation.engine import RuleEngine
from app.validation.rules import get_standard_rules

log = get_logger(__name__)


@dataclass
class PipelineRunResult:
    total_received:      int = 0
    accepted:            int = 0
    duplicates_skipped:  int = 0
    rejected:            int = 0
    late_flagged:        int = 0
    recompute_triggered: bool = False
    issue_summary:       dict[str, int] = field(default_factory=dict)
    errors:              list[str]      = field(default_factory=list)

    def record_issue(self, issue_type: str) -> None:
        self.issue_summary[issue_type] = self.issue_summary.get(issue_type, 0) + 1

    def to_dict(self) -> dict:
        return {
            "total_received":      self.total_received,
            "accepted":            self.accepted,
            "duplicates_skipped":  self.duplicates_skipped,
            "rejected":            self.rejected,
            "late_flagged":        self.late_flagged,
            "recompute_triggered": self.recompute_triggered,
            "issue_summary":       self.issue_summary,
            "errors":              self.errors,
        }


def run_pipeline(
    raw_records: list[dict],
    source_name: str,
    db: Session,
) -> PipelineRunResult:
    result = PipelineRunResult(total_received=len(raw_records))
    ingested_at = datetime.now(timezone.utc)
    all_issues: list[dict] = []
    engine = RuleEngine(rules=get_standard_rules(), source_name=source_name)

    # ── Stage 1: Schema drift ──────────────────────────────
    resolved_records, drift_issues = schema_drift.resolve_batch(raw_records, source_name, db)
    all_issues.extend(drift_issues)
    for issue in drift_issues:
        result.record_issue(issue["issue_type"])

    # ── Stage 2: Validation ────────────────────────────────
    validated, rejected_records, validation_issues = engine.run_batch(resolved_records)
    all_issues.extend(validation_issues)
    result.rejected += len(rejected_records)
    for issue in validation_issues:
        result.record_issue(issue["issue_type"])
    for rec in rejected_records:
        reasons = rec.get("_rejection_reasons", ["unknown"])
        result.errors.append(
            f"Record rejected ({', '.join(reasons)}): "
            f"date={rec.get('transaction_date')}, rev={rec.get('revenue')}"
        )

    if not validated:
        _flush_issues(all_issues, source_name, db)
        return result

    # ── Stage 3: Deduplication ─────────────────────────────
    new_records, dupes, dedup_issues = deduplication.filter_duplicates(
        validated, source_name, db
    )
    all_issues.extend(dedup_issues)
    result.duplicates_skipped += len(dupes)
    for issue in dedup_issues:
        result.record_issue(issue["issue_type"])

    if not new_records:
        _flush_issues(all_issues, source_name, db)
        return result

    # ── Stage 4: Normalisation ─────────────────────────────
    normalised_records = []
    for rec in new_records:
        currency = str(rec.get("currency", "USD")).upper()
        txn_date = rec["transaction_date"]
        revenue  = float(rec.get("revenue") or 0)

        rev_usd, fx_rate, fx_src, fx_issues = normalization.normalize_currency(
            revenue, currency, txn_date, db
        )
        all_issues.extend([{**i, "source_name": source_name} for i in fx_issues])
        for i in fx_issues:
            result.record_issue(i["issue_type"])

        unit = rec.pop("unit", None)
        qty  = int(rec.get("quantity", 1))
        canon_qty, unit_issues = normalization.normalize_quantity(qty, unit, db)
        all_issues.extend([{**i, "source_name": source_name} for i in unit_issues])
        for i in unit_issues:
            result.record_issue(i["issue_type"])

        normalised_records.append({
            **rec,
            "revenue_usd":       rev_usd,
            "revenue_original":  revenue,
            "currency_original": currency,
            "fx_rate":           fx_rate,
            "quantity":          canon_qty,
        })

    # ── Stage 5: Late arrival classification ───────────────
    late_dates: list = []
    for rec in normalised_records:
        txn_date = rec["transaction_date"]
        classification = late_arrivals.classify_lateness(txn_date, ingested_at)
        rec["_lateness"] = classification
        issues_for_rec = late_arrivals.build_late_issues(rec, classification, source_name)
        all_issues.extend(issues_for_rec)
        for i in issues_for_rec:
            result.record_issue(i["issue_type"])
        if classification["requires_recompute"]:
            late_dates.append(txn_date)
            result.late_flagged += 1

    # ── Stage 6: Persist ───────────────────────────────────
    _flush_issues(all_issues, source_name, db)

    staging_ids = _insert_staging(normalised_records, source_name, db)

    deduplication.register_fingerprints(normalised_records, source_name, staging_ids, db)

    for rec, sid in zip(normalised_records, staging_ids):
        lateness = rec.get("_lateness", {})
        if lateness.get("requires_recompute"):
            late_arrivals.record_late_arrival(
                db=db,
                staging_id=sid,
                transaction_date=rec["transaction_date"],
                ingested_at=ingested_at,
                source_name=source_name,
                lateness_days=lateness["lateness_days"],
                severity=lateness["severity"],
            )

    affected_dates = _load_to_fact(normalised_records, source_name, db)

    all_affected = list(set(affected_dates + late_dates))
    if all_affected:
        recompute_result = late_arrivals.recompute_aggregates(db, all_affected)
        result.recompute_triggered = recompute_result.get("recomputed", False)
    else:
        # No new or late records this batch, but still ensure the aggregate
        # table is populated (e.g. after a DB reset where fact_transactions
        # has data but agg_revenue_daily is empty).
        try:
            db.execute(text("SELECT refresh_agg_revenue_daily(NULL)"))
            db.commit()
        except Exception:
            pass  # best-effort; don't fail the pipeline over an aggregate refresh

    result.accepted = len(normalised_records)

    log.info(
        "pipeline.complete",
        source=source_name,
        **result.to_dict(),
    )
    return result


# ── Private helpers ───────────────────────────────────────

def _insert_staging(records: list[dict], source_name: str, db: Session) -> list[int]:
    ids: list[int] = []
    for rec in records:
        sid = db.execute(
            text("""
                INSERT INTO staging_transactions
                    (source_name, raw_payload, transaction_date, sku_id,
                     category_name, region_name, store_id,
                     revenue, quantity, currency, processed)
                VALUES
                    (:source, :payload, :date, :sku, :cat, :region, :store,
                     :revenue, :qty, :currency, FALSE)
                RETURNING staging_id
            """),
            {
                "source":   source_name,
                "payload":  json.dumps(
                    {k: str(v) for k, v in rec.items() if not k.startswith("_")},
                    default=str,
                ),
                "date":     rec["transaction_date"],
                "sku":      rec.get("sku_id"),
                "cat":      rec.get("category_name", "Unknown"),
                "region":   rec.get("region_name", "Unknown"),
                "store":    rec.get("store_id"),
                "revenue":  rec.get("revenue_usd", rec.get("revenue", 0)),
                "qty":      rec.get("quantity", 1),
                "currency": "USD",
            },
        ).scalar()
        ids.append(sid)
    db.commit()
    return ids


def _get_or_create(db: Session, table: str, name_col: str, value: str, id_col: str) -> int:
    row = db.execute(
        text(f"SELECT {id_col} FROM {table} WHERE {name_col} = :v"),
        {"v": value},
    ).first()
    if row:
        return row[0]
    new_id = db.execute(
        text(f"INSERT INTO {table} ({name_col}) VALUES (:v) RETURNING {id_col}"),
        {"v": value},
    ).scalar()
    db.commit()
    return new_id


def _resolve_sku_surrogate(db: Session, sku_id: Optional[str], txn_date) -> Optional[int]:
    if not sku_id:
        return None
    row = db.execute(
        text("""
            SELECT sku_surrogate_id FROM dim_sku
            WHERE sku_id = :sku
              AND :date BETWEEN valid_from AND valid_to
            ORDER BY valid_from DESC LIMIT 1
        """),
        {"sku": sku_id, "date": txn_date},
    ).first()
    return row[0] if row else None


def _load_to_fact(records: list[dict], source_name: str, db: Session) -> list:
    affected: list = []
    source_id = _get_or_create(db, "dim_source", "source_name", source_name, "source_id")

    for rec in records:
        txn_date    = rec["transaction_date"]
        cat_name    = rec.get("category_name", "Unknown")
        region_name = rec.get("region_name", "Unknown")

        cat_id    = _get_or_create(db, "dim_product_category", "category_name", cat_name,    "category_id")
        region_id = _get_or_create(db, "dim_region",           "region_name",   region_name, "region_id")
        sku_sid   = _resolve_sku_surrogate(db, rec.get("sku_id"), txn_date)

        db.execute(
            text("""
                INSERT INTO fact_transactions
                    (transaction_date, sku_surrogate_id, category_id, region_id,
                     store_id, source_id, revenue, revenue_original,
                     currency_original, fx_rate, quantity)
                VALUES
                    (:date, :sku, :cat, :region,
                     :store, :source, :revenue, :rev_orig,
                     :ccy_orig, :fx, :qty)
            """),
            {
                "date":     txn_date,
                "sku":      sku_sid,
                "cat":      cat_id,
                "region":   region_id,
                "store":    rec.get("store_id"),
                "source":   source_id,
                "revenue":  rec.get("revenue_usd", rec.get("revenue", 0)),
                "rev_orig": rec.get("revenue_original"),
                "ccy_orig": rec.get("currency_original"),
                "fx":       rec.get("fx_rate"),
                "qty":      rec.get("quantity", 1),
            },
        )
        affected.append(txn_date)

    db.commit()
    return affected


def _flush_issues(issues: list[dict], source_name: str, db: Session) -> None:
    if not issues:
        return
    for issue in issues:
        db.execute(
            text("""
                INSERT INTO dq_issues
                    (source_name, issue_type, issue_detail,
                     raw_value, corrected_value, severity, auto_corrected)
                VALUES
                    (:src, :type, :detail, :raw, :corr, :sev, :auto)
            """),
            {
                "src":    issue.get("source_name", source_name),
                "type":   issue.get("issue_type", "unknown"),
                "detail": issue.get("issue_detail"),
                "raw":    issue.get("raw_value"),
                "corr":   issue.get("corrected_value"),
                "sev":    issue.get("severity", "warning"),
                "auto":   issue.get("auto_corrected", False),
            },
        )
    db.commit()
