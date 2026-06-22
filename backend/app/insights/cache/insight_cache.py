"""
Insight cache — SHA-256 keyed, per-type TTL cache.

Avoids re-running identical LLM calls within the TTL window.
Cache key = SHA-256(insight_type + segment_key + params).

TTL defaults:
  trend      → 4 hours
  root_cause → 3 hours
  forecast   → 2 hours
  driver     → 4 hours
  executive  → 6 hours
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.insights.engines.insight_engines import InsightResult

log = get_logger(__name__)

_TTL_HOURS: dict[str, int] = {
    "trend":      4,
    "root_cause": 3,
    "forecast":   2,
    "driver":     4,
    "executive":  6,
}


def _make_key(insight_type: str, segment_key: str, params: dict) -> str:
    raw = json.dumps(
        {"type": insight_type, "segment": segment_key, "params": params},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def cache_get(
    db: Session,
    insight_type: str,
    segment_key:  str,
    params:       dict,
) -> Optional[InsightResult]:
    key = _make_key(insight_type, segment_key, params)
    row = db.execute(
        text("SELECT * FROM insight_cache WHERE cache_key=:k AND expires_at > now()"),
        {"k": key},
    ).mappings().first()

    if not row:
        return None

    db.execute(
        text("UPDATE insight_cache SET hit_count=hit_count+1, last_hit_at=now() WHERE cache_key=:k"),
        {"k": key},
    )
    db.commit()
    log.info("cache.hit", key=key[:16], insight_type=insight_type)

    return InsightResult(
        insight_type=row["insight_type"],
        insight_text=row["insight_text"],
        confidence=float(row["confidence"] or 0),
        structured_data=row["structured_data"] or {},
        model_used=row["model_used"] or "deepseek-chat",
        tokens_total=row["tokens_total"] or 0,
        latency_ms=row["latency_ms"] or 0,
        from_cache=True,
    )


def cache_set(
    db: Session,
    insight_type: str,
    segment_key:  str,
    params:       dict,
    result:       InsightResult,
    question:     Optional[str] = None,
    category_id:  Optional[int] = None,
    region_id:    Optional[int] = None,
) -> None:
    key     = _make_key(insight_type, segment_key, params)
    ttl_h   = _TTL_HOURS.get(insight_type, 3)
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_h)

    db.execute(
        text("""
            INSERT INTO insight_cache
                (cache_key, insight_type, segment_key, category_id, region_id,
                 question, insight_text, structured_data, confidence,
                 model_used, tokens_total, latency_ms, expires_at)
            VALUES
                (:key, :itype, :seg, :cat, :region,
                 :question, :text, CAST(:data AS jsonb), :conf,
                 :model, :tokens, :latency, :expires)
            ON CONFLICT (cache_key) DO UPDATE SET
                insight_text = EXCLUDED.insight_text,
                confidence   = EXCLUDED.confidence,
                expires_at   = EXCLUDED.expires_at,
                hit_count    = 0
        """),
        {
            "key":      key,
            "itype":    insight_type,
            "seg":      segment_key,
            "cat":      category_id,
            "region":   region_id,
            "question": question,
            "text":     result.insight_text,
            "data":     json.dumps(result.structured_data, default=str),
            "conf":     result.confidence,
            "model":    result.model_used,
            "tokens":   result.tokens_total,
            "latency":  result.latency_ms,
            "expires":  expires,
        },
    )
    db.commit()
    log.info("cache.set", key=key[:16], ttl_hours=ttl_h, insight_type=insight_type)
