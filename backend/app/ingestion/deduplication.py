"""
Deduplication via SHA-256 fingerprints.

Each record's fingerprint is derived from its identifying fields.
If a source provides its own record_id (Shopify order ID, POS ref)
that is used as the primary key component — more stable than
matching on amounts.

Batch lookup minimises round-trips: we compute all fingerprints
first, then issue a single IN query to find duplicates.
"""

import hashlib
import json
from datetime import date
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.schemas.base import IssueType

log = get_logger(__name__)


def compute_fingerprint(
    source_name: str,
    transaction_date,
    category_name: str,
    region_name: str,
    revenue: float,
    record_id: Optional[str] = None,
) -> str:
    """
    Deterministic SHA-256 fingerprint.
    Uses source record_id when available (preferred).
    Falls back to a composite of identifying fields.
    """
    if record_id:
        key = f"{source_name}::{record_id}"
    else:
        key = json.dumps(
            {
                "source":    source_name,
                "date":      str(transaction_date),
                "category":  str(category_name or "").strip().lower(),
                "region":    str(region_name or "").strip().lower(),
                "revenue":   round(float(revenue or 0), 2),
            },
            sort_keys=True,
        )
    return hashlib.sha256(key.encode()).hexdigest()


def filter_duplicates(
    records: list[dict],
    source_name: str,
    db: Session,
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Split records into (new, duplicates, issues).

    - Attaches `_fingerprint` to each new record so the caller
      can register it after successful staging insert.
    - Handles within-batch duplicates (same record submitted twice
      in the same payload).
    """
    # Compute fingerprints for the whole batch
    fingerprints = [
        compute_fingerprint(
            source_name=source_name,
            transaction_date=r.get("transaction_date"),
            category_name=r.get("category_name", ""),
            region_name=r.get("region_name", ""),
            revenue=float(r.get("revenue") or 0),
            record_id=r.get("record_id") or r.get("id") or r.get("order_id"),
        )
        for r in records
    ]

    # Batch DB lookup — single query
    existing_set: set[str] = set()
    if fingerprints:
        placeholders = ", ".join(f":fp{i}" for i in range(len(fingerprints)))
        params = {f"fp{i}": fp for i, fp in enumerate(fingerprints)}
        rows = db.execute(
            text(
                f"SELECT fingerprint FROM ingestion_fingerprints "
                f"WHERE fingerprint IN ({placeholders})"
            ),
            params,
        ).fetchall()
        existing_set = {r[0] for r in rows}

    new_records:  list[dict] = []
    duplicates:   list[dict] = []
    issues:       list[dict] = []
    seen_in_batch: set[str]  = set()

    for record, fp in zip(records, fingerprints):
        if fp in existing_set or fp in seen_in_batch:
            duplicates.append({**record, "_fingerprint": fp})
            issues.append({
                "issue_type":     IssueType.DUPLICATE.value,
                "source_name":    source_name,
                "issue_detail":   f"Duplicate fingerprint {fp[:16]}... — skipped",
                "raw_value":      str(record.get("transaction_date")),
                "corrected_value": None,
                "severity":       "info",
                "auto_corrected": True,
            })
        else:
            new_records.append({**record, "_fingerprint": fp})
            seen_in_batch.add(fp)

    log.info(
        "dedup.result",
        source=source_name,
        total=len(records),
        new=len(new_records),
        dupes=len(duplicates),
    )
    return new_records, duplicates, issues


def register_fingerprints(
    records: list[dict],
    source_name: str,
    staging_ids: list[int],
    db: Session,
) -> None:
    """Register fingerprints after successful staging inserts."""
    for record, staging_id in zip(records, staging_ids):
        fp = record.get("_fingerprint")
        if not fp:
            continue
        db.execute(
            text("""
                INSERT INTO ingestion_fingerprints (fingerprint, source_name, staging_id)
                VALUES (:fp, :source, :sid)
                ON CONFLICT (fingerprint) DO NOTHING
            """),
            {"fp": fp, "source": source_name, "sid": staging_id},
        )
