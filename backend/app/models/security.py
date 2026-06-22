"""Phase 5 -- security and production-ops ORM models."""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Role(Base):
    __tablename__ = "roles"

    role_id:     Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_name:   Mapped[str]           = mapped_column(String(40), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text)


class User(Base):
    __tablename__ = "users"

    user_id:         Mapped[int]               = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email:            Mapped[str]               = mapped_column(String(255), nullable=False, unique=True)
    hashed_password:  Mapped[str]               = mapped_column(String(255), nullable=False)
    full_name:        Mapped[Optional[str]]     = mapped_column(String(255))
    role_id:          Mapped[int]               = mapped_column(Integer, ForeignKey("roles.role_id"), nullable=False)
    is_active:        Mapped[bool]              = mapped_column(Boolean, default=True)
    created_at:       Mapped[datetime]          = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at:    Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"

    audit_id:    Mapped[int]               = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime]          = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id:     Mapped[Optional[int]]     = mapped_column(BigInteger, ForeignKey("users.user_id"))
    user_email:  Mapped[Optional[str]]     = mapped_column(String(255))
    action:      Mapped[str]               = mapped_column(String(80), nullable=False)
    method:      Mapped[Optional[str]]     = mapped_column(String(10))
    path:        Mapped[Optional[str]]     = mapped_column(Text)
    status_code: Mapped[Optional[int]]     = mapped_column(Integer)
    ip_address:  Mapped[Optional[str]]     = mapped_column(String(64))
    duration_ms: Mapped[Optional[int]]     = mapped_column(Integer)
    detail:      Mapped[Optional[dict]]    = mapped_column(JSONB)


class RetrainingScheduleLog(Base):
    __tablename__ = "retraining_schedule_log"

    schedule_log_id:  Mapped[int]               = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    triggered_at:     Mapped[datetime]          = mapped_column(DateTime(timezone=True), server_default=func.now())
    trigger_reason:   Mapped[str]               = mapped_column(String(40), nullable=False)
    decision:         Mapped[str]               = mapped_column(String(20), nullable=False)
    skip_reason:      Mapped[Optional[str]]     = mapped_column(Text)
    training_run_id:  Mapped[Optional[int]]     = mapped_column(BigInteger, ForeignKey("training_runs.run_id"))
    segments_checked: Mapped[Optional[int]]     = mapped_column(Integer)
    segments_drifted: Mapped[Optional[int]]     = mapped_column(Integer)
    duration_ms:      Mapped[Optional[int]]     = mapped_column(Integer)
