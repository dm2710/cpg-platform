"""
Conversational Analytics Engine — Phase 4.

Lightweight multi-turn Q&A over CPG revenue data, backed by DeepSeek.
No RAG/embeddings — context is built directly from PostgreSQL using
the same context builders as the five Phase 3 insight engines, plus
simple keyword routing to decide which numbers to pull for a given
question.

Handles questions like:
  "Which category will generate the highest revenue next quarter?"
  "Why is dairy forecasted to decline?"
  "Compare snacks and beverages."
  "Which regions are underperforming?"
  "What are the top growth opportunities?"
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.insights.context.builders import (
    build_forecast_context,
    build_revenue_context,
    build_signal_context,
    resolve_segment_label,
)
from app.insights.engines.insight_engines import _extract_confidence
from app.insights.llm.client import get_llm_client

log = get_logger(__name__)

_SYSTEM_PROMPT = """You are an expert CPG (Consumer Packaged Goods) analytics assistant.
You have access to real revenue data, sales forecasts, and active promotions.

STRICT RULES — never break these:
1. Every number you cite MUST come from the provided context data. Never invent figures.
2. If data is missing or insufficient, state exactly what is absent.
3. Structure every answer in this format:
   ANSWER: [1-2 sentences — direct response]
   EVIDENCE: [2-4 bullets citing specific numbers from the context]
   CAVEATS: [1 sentence on limitations, or "None" if fully grounded]
4. End with: CONFIDENCE: 0.XX
5. For comparisons use consistent metrics (% of total, growth rate, absolute $).
6. For opportunities rank by revenue potential combined with trend momentum.
7. For risks rank by magnitude of potential revenue impact.

Be direct, analytical, and evidence-driven. Do not hedge beyond what the data warrants."""


class ConversationEngine:
    def __init__(self, db: Session):
        self._db     = db
        self._client = get_llm_client()

    # ── Session lifecycle ─────────────────────────────────

    def create_session(
        self,
        category_id: Optional[int] = None,
        region_id:   Optional[int] = None,
        title:       Optional[str] = None,
    ) -> str:
        sid   = str(uuid.uuid4())
        seg   = resolve_segment_label(self._db, category_id, region_id) if (category_id or region_id) else "All segments"
        title = title or f"Analytics — {seg}"

        self._db.execute(
            text("""
                INSERT INTO conversation_sessions
                    (session_id, title, segment_key, category_id, region_id)
                VALUES (:sid, :title, :seg, :cat, :reg)
            """),
            {"sid": sid, "title": title, "seg": seg, "cat": category_id, "reg": region_id},
        )
        self._db.commit()
        log.info("conversation.created", session_id=sid)
        return sid

    def get_session(self, session_id: str) -> Optional[dict]:
        row = self._db.execute(
            text("SELECT * FROM conversation_sessions WHERE session_id=:sid"),
            {"sid": session_id},
        ).mappings().first()
        return dict(row) if row else None

    def get_history(self, session_id: str, last_n: int = 10) -> list[dict]:
        rows = self._db.execute(
            text("""
                SELECT role, content, confidence, created_at
                FROM conversation_messages
                WHERE session_id=:sid
                ORDER BY created_at DESC LIMIT :n
            """),
            {"sid": session_id, "n": last_n},
        ).mappings().all()
        return [dict(r) for r in reversed(rows)]

    def list_sessions(self, active_only: bool = True) -> list[dict]:
        where = "WHERE is_active=TRUE" if active_only else ""
        rows  = self._db.execute(
            text(f"""
                SELECT session_id, title, segment_key, created_at,
                       last_active_at, message_count, is_active
                FROM conversation_sessions {where}
                ORDER BY last_active_at DESC LIMIT 100
            """)
        ).mappings().all()
        return [dict(r) for r in rows]

    def delete_session(self, session_id: str) -> bool:
        self._db.execute(text("DELETE FROM conversation_messages WHERE session_id=:sid"), {"sid": session_id})
        result = self._db.execute(text("DELETE FROM conversation_sessions WHERE session_id=:sid"), {"sid": session_id})
        self._db.commit()
        return result.rowcount > 0

    # ── Main ask ──────────────────────────────────────────

    async def ask(
        self,
        session_id:  str,
        question:    str,
        category_id: Optional[int] = None,
        region_id:   Optional[int] = None,
    ) -> dict:
        t0      = time.monotonic()
        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        cat_id = category_id or session.get("category_id")
        reg_id = region_id   or session.get("region_id")

        # 1. Build grounded DB context based on question intent
        db_ctx = self._build_db_context(question, cat_id, reg_id)

        # 2. Build a single prompt combining history + new question
        #    (DeepSeek chat endpoint accepts multi-turn message arrays
        #     just like OpenAI-style APIs, but to keep the client simple
        #     we fold prior turns into the user prompt as plain text.)
        history     = self.get_history(session_id, last_n=6)
        history_txt = "\n".join(f"{h['role'].upper()}: {h['content']}" for h in history) if history else ""

        user_prompt = self._assemble_user_message(question, db_ctx, history_txt)

        # 3. Call DeepSeek
        resp = await self._client.complete(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.35,
            max_tokens=1200,
        )

        clean, confidence = _extract_confidence(resp.text)
        latency = int((time.monotonic() - t0) * 1000)

        # 4. Persist both turns
        self._save_message(session_id, "user", question)
        self._save_message(session_id, "assistant", clean, confidence=confidence, tokens=resp.tokens_total)

        # 5. Update session
        self._db.execute(
            text("UPDATE conversation_sessions SET last_active_at=now(), message_count=message_count+2 WHERE session_id=:sid"),
            {"sid": session_id},
        )
        self._db.commit()

        log.info("conversation.answered", session=session_id, question=question[:60],
                 confidence=confidence, latency_ms=latency)

        return {
            "session_id":   session_id,
            "question":     question,
            "answer":       clean,
            "confidence":   confidence,
            "model_used":   resp.model,
            "tokens_total": resp.tokens_total,
            "latency_ms":   latency,
        }

    # ── DB context builder (keyword routing) ──────────────

    def _build_db_context(self, question: str, category_id: Optional[int], region_id: Optional[int]) -> dict:
        q   = question.lower()
        ctx: dict = {}

        if any(w in q for w in ["revenue", "sales", "trend", "grew", "growth", "decline", "fell", "quarter",
                                 "month", "year", "performance"]):
            ctx["revenue"] = build_revenue_context(self._db, category_id, region_id, lookback_days=90)

        if any(w in q for w in ["forecast", "predict", "next quarter", "future", "outlook", "will generate", "expected"]):
            ctx["forecast"] = build_forecast_context(self._db, category_id, region_id, horizon_days=90)

        if any(w in q for w in ["promo", "campaign", "discount", "marketing", "offer"]):
            ctx["signals"] = build_signal_context(self._db, category_id, region_id)

        if any(w in q for w in ["compare", "vs", "versus", "snack", "beverage", "dairy", "category",
                                 "categories", "highest", "lowest"]):
            rows = self._db.execute(text("""
                SELECT dpc.category_name,
                       SUM(a.total_revenue) AS revenue_90d,
                       SUM(CASE WHEN a.agg_date >= CURRENT_DATE-30 THEN a.total_revenue ELSE 0 END) AS last_30d,
                       SUM(CASE WHEN a.agg_date BETWEEN CURRENT_DATE-60 AND CURRENT_DATE-31 THEN a.total_revenue ELSE 0 END) AS prior_30d
                FROM agg_revenue_daily a
                JOIN dim_product_category dpc ON dpc.category_id=a.category_id
                WHERE a.agg_date >= CURRENT_DATE - 90
                GROUP BY dpc.category_name ORDER BY revenue_90d DESC
            """)).mappings().all()
            ctx["category_comparison"] = [
                {
                    "category":    r["category_name"],
                    "revenue_90d": round(float(r["revenue_90d"]), 2),
                    "last_30d":    round(float(r["last_30d"]), 2),
                    "mom_pct":     round((float(r["last_30d"]) - float(r["prior_30d"])) / float(r["prior_30d"]) * 100, 1)
                                   if float(r["prior_30d"]) else None,
                }
                for r in rows
            ]

        if any(w in q for w in ["region", "regions", "geography", "market", "underperform",
                                 "north america", "europe", "asia"]):
            rows = self._db.execute(text("""
                SELECT dr.region_name,
                       SUM(a.total_revenue) AS revenue_90d,
                       SUM(CASE WHEN a.agg_date >= CURRENT_DATE-30 THEN a.total_revenue ELSE 0 END) AS last_30d,
                       SUM(CASE WHEN a.agg_date BETWEEN CURRENT_DATE-60 AND CURRENT_DATE-31 THEN a.total_revenue ELSE 0 END) AS prior_30d
                FROM agg_revenue_daily a
                JOIN dim_region dr ON dr.region_id=a.region_id
                WHERE a.agg_date >= CURRENT_DATE - 90
                GROUP BY dr.region_name ORDER BY revenue_90d DESC
            """)).mappings().all()
            ctx["region_comparison"] = [
                {
                    "region":      r["region_name"],
                    "revenue_90d": round(float(r["revenue_90d"]), 2),
                    "last_30d":    round(float(r["last_30d"]), 2),
                    "mom_pct":     round((float(r["last_30d"]) - float(r["prior_30d"])) / float(r["prior_30d"]) * 100, 1)
                                   if float(r["prior_30d"]) else None,
                }
                for r in rows
            ]

        if any(w in q for w in ["opportunit", "growth", "potential", "expand", "fastest"]):
            rows = self._db.execute(text("""
                SELECT dpc.category_name,
                       SUM(CASE WHEN a.agg_date >= CURRENT_DATE-30 THEN a.total_revenue ELSE 0 END) AS last_30d,
                       SUM(CASE WHEN a.agg_date BETWEEN CURRENT_DATE-60 AND CURRENT_DATE-31 THEN a.total_revenue ELSE 0 END) AS prior_30d
                FROM agg_revenue_daily a
                JOIN dim_product_category dpc ON dpc.category_id=a.category_id
                WHERE a.agg_date >= CURRENT_DATE - 90
                GROUP BY dpc.category_name
            """)).mappings().all()
            ctx["growth_opportunities"] = sorted([
                {
                    "category": r["category_name"],
                    "last_30d": round(float(r["last_30d"]), 2),
                    "mom_pct":  round((float(r["last_30d"]) - float(r["prior_30d"])) / float(r["prior_30d"]) * 100, 1)
                                if float(r["prior_30d"]) else None,
                }
                for r in rows
            ], key=lambda x: (x["mom_pct"] or 0), reverse=True)

        if not ctx:
            ctx["revenue"] = build_revenue_context(self._db, category_id, region_id, lookback_days=30)

        return ctx

    def _assemble_user_message(self, question: str, db_ctx: dict, history_txt: str) -> str:
        parts = []
        if history_txt:
            parts.append(f"PRIOR CONVERSATION:\n{history_txt}")
        parts.append(f"LIVE DATABASE CONTEXT:\n{json.dumps(db_ctx, indent=2, default=str)}")
        return "\n\n".join(parts) + f"\n\nQUESTION: {question}"

    def _save_message(
        self,
        session_id: str,
        role:       str,
        content:    str,
        confidence: Optional[float] = None,
        tokens:     int = 0,
    ) -> None:
        self._db.execute(
            text("""
                INSERT INTO conversation_messages (session_id, role, content, confidence, tokens)
                VALUES (:sid, :role, :content, :conf, :tok)
            """),
            {"sid": session_id, "role": role, "content": content, "conf": confidence, "tok": tokens},
        )
