"""
Ingestion endpoints.

POST /ingestion/upload-csv          — upload a CSV file from any source
POST /ingestion/push                — push JSON records (webhooks, API)
GET  /ingestion/staging             — inspect staged (unprocessed) records
GET  /ingestion/staging/{staging_id} — single staged record
"""

import io
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.logging import get_logger
from app.ingestion.connectors.source_connectors import get_connector, CsvConnector, JsonPushConnector
from app.pipeline.orchestrator import run_pipeline
from app.schemas.ingestion import (
    CsvUploadResponse,
    PipelineResult,
    PushPayload,
    PushResponse,
    StagingRecordOut,
)
from app.schemas.base import PaginatedResponse
from app.security.deps import CurrentUser, require_permission
from app.security.rbac import Permission

router = APIRouter()
log    = get_logger(__name__)


@router.post(
    "/upload-csv",
    response_model=CsvUploadResponse,
    summary="Upload a CSV transaction file",
)
async def upload_csv(
    source_name: str        = Query(default="csv_upload", max_length=80),
    file:        UploadFile = File(...),
    db:          Session    = Depends(get_db),
    user:        CurrentUser = Depends(require_permission(Permission.INGEST_DATA)),
):
    """
    Accepts a CSV file from any source.
    Column names are resolved via alias mapping — the file does not
    need to use canonical column names.
    """
    if not file.filename or not file.filename.lower().endswith((".csv", ".tsv", ".txt")):
        raise HTTPException(400, "File must be a CSV/TSV/TXT file")

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(400, "Uploaded file is empty")

    connector = get_connector(source_name)
    if not isinstance(connector, CsvConnector):
        # Use generic CSV connector for unknown sources
        connector = CsvConnector(source_name)

    try:
        records = connector.run(content)
    except ValueError as exc:
        raise HTTPException(422, f"Could not parse CSV: {exc}")

    rows_detected = len(records)
    if rows_detected == 0:
        raise HTTPException(422, "No data rows found in CSV after parsing")

    result = run_pipeline(records, source_name, db)

    log.info(
        "ingestion.csv_upload",
        source=source_name,
        filename=file.filename,
        rows=rows_detected,
    )

    return CsvUploadResponse(
        source_name=source_name,
        filename=file.filename,
        rows_detected=rows_detected,
        pipeline_result=PipelineResult(**result.to_dict()),
    )


@router.post(
    "/push",
    response_model=PushResponse,
    summary="Push JSON records from any source",
)
def push_records(
    payload: PushPayload,
    db:      Session = Depends(get_db),
    user:    CurrentUser = Depends(require_permission(Permission.INGEST_DATA)),
):
    """
    Accepts a batch of raw JSON records.
    Each record may use any field naming convention —
    schema drift resolution handles the mapping.
    """
    # Group by source_name (records may declare their own source)
    by_source: dict[str, list[dict]] = {}
    for rec in payload.records:
        src = rec.get("source_name") or payload.source_name
        by_source.setdefault(src, []).append(rec)

    combined = PipelineResult(
        total_received=0, accepted=0, duplicates_skipped=0,
        rejected=0, late_flagged=0, recompute_triggered=False,
    )

    for source_name, records in by_source.items():
        result = run_pipeline(records, source_name, db)
        d = result.to_dict()
        combined.total_received     += d["total_received"]
        combined.accepted           += d["accepted"]
        combined.duplicates_skipped += d["duplicates_skipped"]
        combined.rejected           += d["rejected"]
        combined.late_flagged       += d["late_flagged"]
        combined.recompute_triggered = combined.recompute_triggered or d["recompute_triggered"]
        for k, v in d["issue_summary"].items():
            combined.issue_summary[k] = combined.issue_summary.get(k, 0) + v
        combined.errors.extend(d["errors"])

    return PushResponse(
        sources_processed=len(by_source),
        pipeline_result=combined,
    )


@router.get(
    "/staging",
    response_model=PaginatedResponse[StagingRecordOut],
    summary="List staged transactions",
)
def list_staging(
    source_name: Optional[str] = None,
    processed:   Optional[bool] = None,
    page:        int  = Query(default=1, ge=1),
    page_size:   int  = Query(default=50, ge=1, le=500),
    db:          Session = Depends(get_db),
):
    filters = []
    params: dict = {}

    if source_name:
        filters.append("source_name = :source")
        params["source"] = source_name
    if processed is not None:
        filters.append("processed = :processed")
        params["processed"] = processed

    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    offset = (page - 1) * page_size

    total = db.execute(
        text(f"SELECT COUNT(*) FROM staging_transactions {where}"), params
    ).scalar()

    rows = db.execute(
        text(f"""
            SELECT staging_id, source_name, transaction_date, category_name,
                   region_name, revenue, quantity, currency, processed,
                   ingested_at, error_message
            FROM staging_transactions {where}
            ORDER BY staging_id DESC
            LIMIT :limit OFFSET :offset
        """),
        {**params, "limit": page_size, "offset": offset},
    ).mappings().all()

    return PaginatedResponse(
        data=[StagingRecordOut(**dict(r)) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=-(-total // page_size),
    )


@router.get(
    "/staging/{staging_id}",
    response_model=StagingRecordOut,
    summary="Get a single staged record",
)
def get_staging_record(staging_id: int, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT * FROM staging_transactions WHERE staging_id = :id"),
        {"id": staging_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, f"Staging record {staging_id} not found")
    return StagingRecordOut(**dict(row))
