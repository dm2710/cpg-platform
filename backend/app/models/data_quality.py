"""
Data quality tracking models.

ingestion_fingerprints : deduplication store (SHA-256 per record)
late_arrivals          : audit log for records arriving after their window
dq_issues              : every detected problem with severity + correction
field_aliases          : per-source column-name mapping for schema drift
fx_rates               : daily FX rate table (USD base)
unit_mappings          : quantity unit → canonical unit + multiplier
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey,
    Integer, Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


# ── ingestion_fingerprints ────────────────────────────────

class IngestionFingerprint(Base):
    __tablename__ = "ingestion_fingerprints"

    fingerprint:   Mapped[str]           = mapped_column(String(64),  primary_key=True)
    source_name:   Mapped[str]           = mapped_column(String(80),  nullable=False, index=True)
    staging_id:    Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("staging_transactions.staging_id"))
    first_seen_at: Mapped[datetime]      = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ── late_arrivals ─────────────────────────────────────────

class LateArrival(Base):
    __tablename__ = "late_arrivals"

    id:               Mapped[int]              = mapped_column(Integer, primary_key=True, autoincrement=True)
    staging_id:       Mapped[Optional[int]]    = mapped_column(BigInteger, ForeignKey("staging_transactions.staging_id"))
    transaction_date: Mapped[date]             = mapped_column(Date, nullable=False, index=True)
    ingested_at:      Mapped[datetime]         = mapped_column(DateTime(timezone=True), nullable=False)
    lateness_days:    Mapped[int]              = mapped_column(Integer, nullable=False)
    severity:         Mapped[str]              = mapped_column(String(20), nullable=False)  # soft_late, late, very_late
    source_name:      Mapped[str]              = mapped_column(String(80), nullable=False)
    resolved:         Mapped[bool]             = mapped_column(Boolean, default=False, nullable=False, index=True)
    resolved_at:      Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ── dq_issues ─────────────────────────────────────────────

class DqIssue(Base):
    __tablename__ = "dq_issues"

    id:               Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    staging_id:       Mapped[Optional[int]] = mapped_column(BigInteger)
    source_name:      Mapped[str]           = mapped_column(String(80),  nullable=False, index=True)
    issue_type:       Mapped[str]           = mapped_column(String(80),  nullable=False, index=True)
    issue_detail:     Mapped[Optional[str]] = mapped_column(Text)
    raw_value:        Mapped[Optional[str]] = mapped_column(Text)
    corrected_value:  Mapped[Optional[str]] = mapped_column(Text)
    severity:         Mapped[str]           = mapped_column(String(20),  nullable=False, default="warning")
    auto_corrected:   Mapped[bool]          = mapped_column(Boolean, default=False)
    detected_at:      Mapped[datetime]      = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


# ── field_aliases ─────────────────────────────────────────

class FieldAlias(Base):
    __tablename__ = "field_aliases"
    __table_args__ = (
        UniqueConstraint("source_name", "source_field", name="uq_field_alias"),
    )

    id:               Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_name:      Mapped[str] = mapped_column(String(80),  nullable=False)
    source_field:     Mapped[str] = mapped_column(String(120), nullable=False)
    canonical_field:  Mapped[str] = mapped_column(String(80),  nullable=False)


# ── fx_rates ──────────────────────────────────────────────

class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (
        UniqueConstraint("rate_date", "currency", name="uq_fx_rate"),
    )

    id:          Mapped[int]     = mapped_column(Integer, primary_key=True, autoincrement=True)
    rate_date:   Mapped[date]    = mapped_column(Date, nullable=False, index=True)
    currency:    Mapped[str]     = mapped_column(String(3), nullable=False)
    rate_to_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)


# ── unit_mappings ─────────────────────────────────────────

class UnitMapping(Base):
    __tablename__ = "unit_mappings"

    source_unit:    Mapped[str]     = mapped_column(String(40), primary_key=True)
    canonical_unit: Mapped[str]     = mapped_column(String(40), nullable=False)
    multiplier:     Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
