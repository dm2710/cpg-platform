"""Phase 3 — AI Insights ORM models."""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey,
    Integer, Numeric, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class InsightCache(Base):
    __tablename__ = "insight_cache"

    cache_id:        Mapped[int]               = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cache_key:       Mapped[str]               = mapped_column(String(64),  nullable=False, unique=True)
    insight_type:    Mapped[str]               = mapped_column(String(60),  nullable=False)
    segment_key:     Mapped[str]               = mapped_column(String(200), nullable=False)
    category_id:     Mapped[Optional[int]]     = mapped_column(Integer, ForeignKey("dim_product_category.category_id"))
    region_id:       Mapped[Optional[int]]     = mapped_column(Integer, ForeignKey("dim_region.region_id"))
    question:        Mapped[Optional[str]]     = mapped_column(Text)
    insight_text:    Mapped[str]               = mapped_column(Text, nullable=False)
    structured_data: Mapped[Optional[dict]]    = mapped_column(JSONB)
    confidence:      Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 3))
    model_used:      Mapped[Optional[str]]     = mapped_column(String(80))
    tokens_total:    Mapped[Optional[int]]     = mapped_column(Integer)
    latency_ms:      Mapped[Optional[int]]     = mapped_column(Integer)
    generated_at:    Mapped[datetime]          = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at:      Mapped[datetime]          = mapped_column(DateTime(timezone=True), nullable=False)
    hit_count:       Mapped[int]               = mapped_column(Integer, default=0)
    last_hit_at:     Mapped[datetime]          = mapped_column(DateTime(timezone=True), server_default=func.now())


class InsightLog(Base):
    __tablename__ = "insight_log"

    log_id:            Mapped[int]               = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    insight_type:      Mapped[str]               = mapped_column(String(60),  nullable=False)
    segment_key:       Mapped[Optional[str]]     = mapped_column(String(200))
    category_id:       Mapped[Optional[int]]     = mapped_column(Integer, ForeignKey("dim_product_category.category_id"))
    region_id:         Mapped[Optional[int]]     = mapped_column(Integer, ForeignKey("dim_region.region_id"))
    question:          Mapped[Optional[str]]     = mapped_column(Text)
    system_prompt:     Mapped[Optional[str]]     = mapped_column(Text)
    user_prompt:       Mapped[Optional[str]]     = mapped_column(Text)
    insight_text:      Mapped[Optional[str]]     = mapped_column(Text)
    structured_data:   Mapped[Optional[dict]]    = mapped_column(JSONB)
    confidence:        Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 3))
    model_used:        Mapped[Optional[str]]     = mapped_column(String(80))
    tokens_prompt:     Mapped[Optional[int]]     = mapped_column(Integer)
    tokens_completion: Mapped[Optional[int]]     = mapped_column(Integer)
    tokens_total:      Mapped[Optional[int]]     = mapped_column(Integer)
    latency_ms:        Mapped[Optional[int]]     = mapped_column(Integer)
    from_cache:        Mapped[bool]              = mapped_column(Boolean, default=False)
    status:            Mapped[str]               = mapped_column(String(20), default="success")
    error_detail:      Mapped[Optional[str]]     = mapped_column(Text)
    requested_at:      Mapped[datetime]          = mapped_column(DateTime(timezone=True), server_default=func.now())
    triggered_by:      Mapped[Optional[str]]     = mapped_column(String(80))


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"

    session_id:     Mapped[str]            = mapped_column(String(36), primary_key=True)
    title:          Mapped[Optional[str]]  = mapped_column(String(255))
    segment_key:    Mapped[Optional[str]]  = mapped_column(String(200))
    category_id:    Mapped[Optional[int]]  = mapped_column(Integer, ForeignKey("dim_product_category.category_id"))
    region_id:      Mapped[Optional[int]]  = mapped_column(Integer, ForeignKey("dim_region.region_id"))
    created_at:     Mapped[datetime]       = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_active_at: Mapped[datetime]       = mapped_column(DateTime(timezone=True), server_default=func.now())
    message_count:  Mapped[int]            = mapped_column(Integer, default=0)
    is_active:      Mapped[bool]           = mapped_column(Boolean, default=True)


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    message_id:      Mapped[int]              = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id:      Mapped[str]              = mapped_column(String(36), ForeignKey("conversation_sessions.session_id"), nullable=False)
    role:            Mapped[str]              = mapped_column(String(20), nullable=False)
    content:         Mapped[str]              = mapped_column(Text, nullable=False)
    structured_data: Mapped[Optional[dict]]   = mapped_column(JSONB)
    confidence:      Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 3))
    tokens:          Mapped[Optional[int]]    = mapped_column(Integer)
    created_at:      Mapped[datetime]         = mapped_column(DateTime(timezone=True), server_default=func.now())
