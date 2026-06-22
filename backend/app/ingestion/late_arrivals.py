"""
Late arrival handling.

Classifies records by how late they arrive relative to their
transaction_date and triggers aggregate recomputes for affected windows.

Thresholds (configurable via Settings):
  < 3 days  — normal          (pipeline latency)
  3–7 days  — soft_late       — log, recompute on next scheduled run
  7–30 days — late            — log warning, immediate recompute
  > 30 days — very_late       — log error, flag for manual review
"""

from datetime import date, datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.base import IssueType, LatenessSeverity

log = get_logger(__name__)
settings = get_settings()


def classify_lateness(
    transaction_date: date,
    ingested_at: Optional[datetime] = None,
) -> dict:
    now = ingested_at or datetime.now(timezone.utc)
    if isinstance(now, datetime) and now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    txn_dt = datetime(
        transaction_date.year, transaction_date.month,
        transaction_date.day, tzinfo=timezone.utc,
    )
    lateness_days = max(0, (now - txn_dt).days)

    if lateness_days < settings.late_arrival_soft_days:
        severity = LatenessSeverity.NORMAL
    elif lateness_days < settings.late_arrival_hard_days:
        severity = LatenessSeverity.SOFT_LATE
    elif lateness_days < settings.late_arrival_review_days:
        severity = LatenessSeverity.LATE
    else:
        severity = LatenessSeverity.VERY_LATE

    return {
        "lateness_days":      lateness_days,
        "severity":           severity.value,
        "requires_recompute": lateness_days >= settings.late_arrival_soft_days,
        "requires_review":    lateness_days >= settings.late_arrival_review_days,
    }


def record_late_arrival(
    db: Session,
    staging_id: int,
    transaction_date: date,
    ingested_at: datetime,
    source_name: str,
    lateness_days: int,
    severity: str,
) -> int:
    row = db.execute(
        text("""
            INSERT INTO late_arrivals
                (staging_id, transaction_date, ingested_at,
                 lateness_days, severity, source_name)
            VALUES (:sid, :txn, :ing, :days, :sev, :src)
            RETURNING id
        """),
        {
            "sid":  staging_id,
            "txn":  transaction_date,
            "ing":  ingested_at,
            "days": lateness_days,
            "sev":  severity,
            "src":  source_name,
        },
    ).scalar()
    return row


def recompute_aggregates(
    db: Session,
    affected_dates: list[date],
) -> dict:
    """
    Idempotent recompute of agg_revenue_daily from the earliest
    affected date forward, then mark late_arrivals as resolved.
    """
    if not affected_dates:
        return {"recomputed": False, "reason": "no affected dates"}

    min_date = min(affected_dates)

    db.execute(
        text("SELECT refresh_agg_revenue_daily(:since)"),
        {"since": min_date},
    )

    db.execute(
        text("""
            UPDATE late_arrivals
            SET resolved = TRUE, resolved_at = now()
            WHERE transaction_date >= :since AND resolved = FALSE
        """),
        {"since": min_date},
    )
    db.commit()

    log.info(
        "late_arrivals.recomputed",
        from_date=str(min_date),
        affected=len(set(affected_dates)),
    )

    return {
        "recomputed":     True,
        "from_date":      str(min_date),
        "affected_dates": sorted(str(d) for d in set(affected_dates)),
    }


def build_late_issues(
    record: dict,
    classification: dict,
    source_name: str,
) -> list[dict]:
    issues: list[dict] = []
    severity = classification["severity"]
    days     = classification["lateness_days"]
    txn_date = record.get("transaction_date")

    if severity == LatenessSeverity.NORMAL.value:
        return issues

    if severity == LatenessSeverity.VERY_LATE.value:
        issues.append({
            "issue_type":     IssueType.VERY_LATE_ARRIVAL.value,
            "source_name":    source_name,
            "issue_detail":   f"Record for {txn_date} arrived {days} days late — manual review required",
            "raw_value":      str(txn_date),
            "corrected_value": None,
            "severity":       "error",
            "auto_corrected": False,
        })
    else:
        issues.append({
            "issue_type":     IssueType.LATE_ARRIVAL.value,
            "source_name":    source_name,
            "issue_detail":   f"Record for {txn_date} arrived {days} days late (severity: {severity})",
            "raw_value":      str(txn_date),
            "corrected_value": "scheduled_recompute",
            "severity":       "warning",
            "auto_corrected": True,
        })

    return issues
