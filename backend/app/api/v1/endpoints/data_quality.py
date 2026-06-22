"""
Data quality monitoring endpoints.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.ingestion.late_arrivals import recompute_aggregates
from app.schemas.analytics import (
    DqIssueSummaryRow,
    DqIssueRow,
    LateArrivalRow,
    RecomputeResponse,
    SourceHealthRow,
)
from app.schemas.base import PaginatedResponse

router = APIRouter()


@router.get("/summary", response_model=list[DqIssueSummaryRow])
def dq_summary(db: Session = Depends(get_db)):
    """Issue counts by source + type, from the dq_summary view."""
    rows = db.execute(text("SELECT * FROM dq_summary")).mappings().all()
    return [DqIssueSummaryRow(**dict(r)) for r in rows]


@router.get("/issues", response_model=PaginatedResponse[DqIssueRow])
def list_dq_issues(
    source_name: Optional[str] = None,
    issue_type:  Optional[str] = None,
    severity:    Optional[str] = None,
    page:        int           = Query(default=1, ge=1),
    page_size:   int           = Query(default=100, ge=1, le=500),
    db:          Session       = Depends(get_db),
):
    filters = []
    params: dict = {}
    if source_name:
        filters.append("source_name = :source")
        params["source"] = source_name
    if issue_type:
        filters.append("issue_type = :issue_type")
        params["issue_type"] = issue_type
    if severity:
        filters.append("severity = :severity")
        params["severity"] = severity

    where  = f"WHERE {' AND '.join(filters)}" if filters else ""
    offset = (page - 1) * page_size

    total = db.execute(
        text(f"SELECT COUNT(*) FROM dq_issues {where}"), params
    ).scalar()

    rows = db.execute(
        text(f"""
            SELECT id, source_name, issue_type, issue_detail, raw_value,
                   corrected_value, severity, auto_corrected, detected_at
            FROM dq_issues {where}
            ORDER BY detected_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {**params, "limit": page_size, "offset": offset},
    ).mappings().all()

    return PaginatedResponse(
        data=[DqIssueRow(**dict(r)) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=-(-total // page_size),
    )


@router.get("/late-arrivals", response_model=list[LateArrivalRow])
def list_late_arrivals(
    resolved: Optional[bool] = False,
    db:       Session        = Depends(get_db),
):
    where  = "" if resolved is None else ("WHERE resolved = TRUE" if resolved else "WHERE resolved = FALSE")
    rows   = db.execute(
        text(f"""
            SELECT id, transaction_date, ingested_at, lateness_days,
                   severity, source_name, resolved
            FROM late_arrivals {where}
            ORDER BY lateness_days DESC
        """)
    ).mappings().all()
    return [LateArrivalRow(**dict(r)) for r in rows]


@router.post("/recompute", response_model=RecomputeResponse)
def trigger_recompute(
    from_date: date,
    to_date:   Optional[date] = None,
    db:        Session        = Depends(get_db),
):
    """Manually recompute daily aggregates for a date range."""
    from datetime import timedelta
    end    = to_date or date.today()
    dates  = [from_date + timedelta(days=i) for i in range((end - from_date).days + 1)]
    result = recompute_aggregates(db, dates)
    return RecomputeResponse(**result)


@router.get("/sources", response_model=list[SourceHealthRow])
def source_health(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT
            s.source_name,
            COUNT(*)                                        AS total_staged,
            COUNT(*) FILTER (WHERE s.processed)             AS processed,
            MAX(s.ingested_at)                              AS last_ingested_at,
            COUNT(dq.id)                                    AS total_issues,
            COUNT(dq.id) FILTER (WHERE dq.severity='error') AS error_count,
            COUNT(dq.id) FILTER (WHERE dq.severity='warning') AS warning_count
        FROM staging_transactions s
        LEFT JOIN dq_issues dq ON dq.source_name = s.source_name
        GROUP BY s.source_name
        ORDER BY last_ingested_at DESC NULLS LAST
    """)).mappings().all()
    return [SourceHealthRow(**dict(r)) for r in rows]
