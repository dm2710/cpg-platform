"""
Automated retraining admin API.

GET  /retraining/log       -- view the scheduler's run/skip decisions
POST /retraining/trigger   -- manually trigger a retrain right now
                               (bypasses the cron/drift schedule, still
                               respects the same min-new-rows logic
                               unless force=true)
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.base import _to_camel as _to_camel_key
from app.scheduler.retraining import trigger_manual_retrain
from app.security.deps import CurrentUser, require_permission
from app.security.rbac import Permission

router = APIRouter()


@router.get("/log", summary="View the retraining scheduler's decision log")
def retraining_log(
    limit: int = Query(default=50, ge=1, le=500),
    trigger_reason: Optional[str] = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.MANAGE_RETRAINING)),
):
    filters, params = [], {"limit": limit}
    if trigger_reason:
        filters.append("trigger_reason = :reason")
        params["reason"] = trigger_reason
    where = f"WHERE {' AND '.join(filters)}" if filters else ""

    rows = db.execute(
        text(f"""
            SELECT schedule_log_id, triggered_at, trigger_reason, decision,
                   skip_reason, training_run_id, segments_checked,
                   segments_drifted, duration_ms
            FROM retraining_schedule_log {where}
            ORDER BY triggered_at DESC LIMIT :limit
        """),
        params,
    ).mappings().all()
    return [{_to_camel_key(k): v for k, v in dict(r).items()} for r in rows]


@router.post("/trigger", summary="Manually trigger a retrain")
def manual_trigger(
    category_ids: Optional[list[int]] = None,
    region_ids: Optional[list[int]] = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.MANAGE_RETRAINING)),
):
    result = trigger_manual_retrain(db, category_ids=category_ids, region_ids=region_ids)
    return {_to_camel_key(k): v for k, v in result.items()}
