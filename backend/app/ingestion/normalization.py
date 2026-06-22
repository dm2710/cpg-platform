"""
Normalization: currency → USD, quantity units → canonical.

FX rate lookup priority:
  1. Exact date match in fx_rates table
  2. Most recent prior date in fx_rates table
  3. Static in-process fallback (hardcoded bootstrap rates)
  4. Pass-through with error logged

Unit multipliers loaded once from unit_mappings table, cached in-process.
"""

from datetime import date
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.schemas.base import IssueType

log = get_logger(__name__)

# In-process FX cache: (currency, date) → rate
_fx_cache: dict[tuple[str, date], float] = {}

# Static bootstrap rates (USD base)
_STATIC_RATES: dict[str, float] = {
    "USD": 1.0,   "EUR": 1.085, "GBP": 1.27,  "JPY": 0.0067,
    "INR": 0.012, "AUD": 0.65,  "CAD": 0.74,  "SGD": 0.75,
    "AED": 0.272, "BRL": 0.20,  "MXN": 0.058, "CNY": 0.14,
}

# Unit cache: source_unit → (canonical_unit, multiplier)
_unit_cache: dict[str, tuple[str, float]] = {}
_unit_cache_loaded = False


# ── FX normalisation ──────────────────────────────────────

def _get_rate(currency: str, as_of: date, db: Session) -> tuple[float, str]:
    """Returns (rate_to_usd, source_label)."""
    cache_key = (currency, as_of)
    if cache_key in _fx_cache:
        return _fx_cache[cache_key], "cache"

    # Exact date
    row = db.execute(
        text("SELECT rate_to_usd FROM fx_rates WHERE currency = :c AND rate_date = :d"),
        {"c": currency, "d": as_of},
    ).first()
    if row:
        _fx_cache[cache_key] = float(row[0])
        return float(row[0]), "exact"

    # Most recent prior date
    row = db.execute(
        text("""
            SELECT rate_to_usd FROM fx_rates
            WHERE currency = :c AND rate_date <= :d
            ORDER BY rate_date DESC LIMIT 1
        """),
        {"c": currency, "d": as_of},
    ).first()
    if row:
        _fx_cache[cache_key] = float(row[0])
        return float(row[0]), "prior_date"

    # Static fallback
    if currency in _STATIC_RATES:
        rate = _STATIC_RATES[currency]
        _fx_cache[cache_key] = rate
        return rate, "static_fallback"

    return 1.0, "unknown"


def normalize_currency(
    revenue: float,
    currency: str,
    transaction_date: date,
    db: Session,
) -> tuple[float, float, str, list[dict]]:
    """
    Convert revenue to USD.
    Returns (revenue_usd, fx_rate, rate_source, issues).
    """
    issues: list[dict] = []
    currency = (currency or "USD").upper().strip()

    if currency == "USD":
        return revenue, 1.0, "exact", issues

    rate, source = _get_rate(currency, transaction_date, db)

    if source == "unknown":
        issues.append({
            "issue_type":     IssueType.UNKNOWN_CURRENCY.value,
            "issue_detail":   f"No FX rate found for '{currency}' — revenue passed through unchanged",
            "raw_value":      currency,
            "corrected_value": None,
            "severity":       "error",
            "auto_corrected": False,
        })
        return revenue, 1.0, source, issues

    if source == "static_fallback":
        issues.append({
            "issue_type":     IssueType.FX_RATE_FALLBACK.value,
            "issue_detail":   f"Static fallback rate used for {currency} on {transaction_date}: {rate}",
            "raw_value":      currency,
            "corrected_value": str(rate),
            "severity":       "warning",
            "auto_corrected": True,
        })

    revenue_usd = round(revenue * rate, 2)
    return revenue_usd, rate, source, issues


# ── Unit normalisation ────────────────────────────────────

def _load_units(db: Session) -> None:
    global _unit_cache_loaded
    if _unit_cache_loaded:
        return
    rows = db.execute(
        text("SELECT source_unit, canonical_unit, multiplier FROM unit_mappings")
    ).fetchall()
    for row in rows:
        _unit_cache[row[0].lower()] = (row[1], float(row[2]))
    _unit_cache_loaded = True
    log.info("unit_cache.loaded", count=len(_unit_cache))


def normalize_quantity(
    quantity: int,
    unit: Optional[str],
    db: Session,
) -> tuple[int, list[dict]]:
    """
    Convert quantity to canonical units.
    Returns (canonical_qty, issues).
    """
    _load_units(db)
    issues: list[dict] = []

    if not unit:
        return quantity, issues

    unit_lower = unit.lower().strip()

    if unit_lower in ("unit", "units", "each", "ea", "piece", "pc", ""):
        return quantity, issues

    if unit_lower in _unit_cache:
        canonical_unit, multiplier = _unit_cache[unit_lower]
        canonical_qty = max(1, int(round(quantity * multiplier)))
        if multiplier != 1.0:
            issues.append({
                "issue_type":     IssueType.UNIT_CONVERSION.value,
                "issue_detail":   f"{quantity} {unit} → {canonical_qty} {canonical_unit} (×{multiplier})",
                "raw_value":      f"{quantity} {unit}",
                "corrected_value": f"{canonical_qty} {canonical_unit}",
                "severity":       "info",
                "auto_corrected": True,
            })
        return canonical_qty, issues

    issues.append({
        "issue_type":     IssueType.UNKNOWN_UNIT.value,
        "issue_detail":   f"Unknown unit '{unit}' — quantity used as-is",
        "raw_value":      unit,
        "corrected_value": None,
        "severity":       "warning",
        "auto_corrected": False,
    })
    return quantity, issues


def clear_caches() -> None:
    """Reset all in-process caches (use in tests)."""
    global _unit_cache_loaded
    _fx_cache.clear()
    _unit_cache.clear()
    _unit_cache_loaded = False
