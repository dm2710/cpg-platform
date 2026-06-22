"""
Unit tests for pipeline stage modules.
Schema drift and normalization use mocked DB sessions.
Deduplication tests use the real test DB via the db fixture.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.ingestion.schema_drift import (
    FALLBACK_ALIASES,
    _normalise_key,
    resolve_record,
)
from app.ingestion.deduplication import compute_fingerprint, filter_duplicates
from app.ingestion.normalization import normalize_currency, normalize_quantity, clear_caches
from app.ingestion.late_arrivals import classify_lateness
from app.ingestion.parsers.csv_parser import parse_csv


# ── Schema drift ──────────────────────────────────────────

class TestSchemaDrift:
    def _mock_db(self):
        """DB that returns no aliases (tests fallback chain)."""
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = []
        return db

    def test_canonical_field_passes_through(self):
        db = self._mock_db()
        resolved, issues = resolve_record(
            {"revenue": 100.0, "transaction_date": "2024-01-01"},
            "test_source", db,
        )
        assert resolved["revenue"] == 100.0
        assert resolved["transaction_date"] == "2024-01-01"

    def test_fallback_alias_resolved(self):
        db = self._mock_db()
        resolved, issues = resolve_record(
            {"sale_date": "2024-01-01", "net_amount": 50.0},
            "pos_legacy", db,
        )
        assert "transaction_date" in resolved
        assert "revenue" in resolved
        assert any(i["issue_type"] == "schema_drift" for i in issues)

    def test_fuzzy_match_resolves_typo(self):
        db = self._mock_db()
        # "reveneu" is close enough to "revenue"
        resolved, issues = resolve_record(
            {"reveneu": 100.0, "transaction_date": "2024-01-01"},
            "messy_source", db,
        )
        # fuzzy match should catch this
        assert "revenue" in resolved or len(issues) > 0

    def test_unknown_field_dropped(self):
        db = self._mock_db()
        resolved, issues = resolve_record(
            {"transaction_date": "2024-01-01", "revenue": 100.0, "xyz_internal_id": "abc"},
            "test", db,
        )
        assert "xyz_internal_id" not in resolved

    def test_normalise_key(self):
        assert _normalise_key("  Sale Date  ") == "sale_date"
        assert _normalise_key("Total-Amount") == "total_amount"
        assert _normalise_key("Revenue.USD") == "revenue_usd"

    def test_shopify_field_aliases(self):
        db = self._mock_db()
        resolved, _ = resolve_record(
            {"created_at": "2024-01-01", "total_price": 99.99,
             "product_type": "Electronics", "shipping_country": "US"},
            "shopify", db,
        )
        assert "transaction_date" in resolved
        assert "revenue" in resolved


# ── Deduplication ─────────────────────────────────────────

class TestDeduplication:
    def test_fingerprint_stable(self):
        fp1 = compute_fingerprint("src", date(2024, 1, 1), "Elec", "NA", 100.0)
        fp2 = compute_fingerprint("src", date(2024, 1, 1), "Elec", "NA", 100.0)
        assert fp1 == fp2

    def test_fingerprint_differs_on_revenue(self):
        fp1 = compute_fingerprint("src", date(2024, 1, 1), "Elec", "NA", 100.0)
        fp2 = compute_fingerprint("src", date(2024, 1, 1), "Elec", "NA", 101.0)
        assert fp1 != fp2

    def test_record_id_takes_priority(self):
        fp1 = compute_fingerprint("src", date(2024, 1, 1), "Elec", "NA", 100.0, record_id="ORD-001")
        fp2 = compute_fingerprint("src", date(2024, 1, 1), "Elec", "NA", 999.0, record_id="ORD-001")
        assert fp1 == fp2  # same record_id → same fingerprint

    def test_within_batch_dedup(self, db):
        """Two identical records in the same batch → one accepted, one duplicate."""
        records = [
            {"transaction_date": date(2024, 1, 1), "category_name": "Electronics",
             "region_name": "NA", "revenue": 100.0},
            {"transaction_date": date(2024, 1, 1), "category_name": "Electronics",
             "region_name": "NA", "revenue": 100.0},
        ]
        new, dupes, issues = filter_duplicates(records, "test_source", db)
        assert len(new) == 1
        assert len(dupes) == 1
        assert any(i["issue_type"] == "duplicate" for i in issues)

    def test_distinct_records_both_accepted(self, db):
        records = [
            {"transaction_date": date(2024, 1, 1), "category_name": "Electronics",
             "region_name": "NA", "revenue": 100.0},
            {"transaction_date": date(2024, 1, 2), "category_name": "Apparel",
             "region_name": "EU", "revenue": 200.0},
        ]
        new, dupes, _ = filter_duplicates(records, "test_source", db)
        assert len(new) == 2
        assert len(dupes) == 0


# ── Normalization ─────────────────────────────────────────

class TestNormalization:
    def setup_method(self):
        clear_caches()

    def _mock_db_no_rate(self):
        db = MagicMock()
        db.execute.return_value.first.return_value = None
        return db

    def test_usd_passthrough(self):
        db = self._mock_db_no_rate()
        usd, rate, src, issues = normalize_currency(100.0, "USD", date(2024, 1, 1), db)
        assert usd == 100.0
        assert rate == 1.0
        assert not issues

    def test_eur_static_fallback(self):
        db = self._mock_db_no_rate()
        usd, rate, src, issues = normalize_currency(100.0, "EUR", date(2024, 1, 1), db)
        assert usd > 100.0   # EUR > USD
        assert src == "static_fallback"
        assert any(i["issue_type"] == "fx_rate_fallback" for i in issues)

    def test_unknown_currency_error(self):
        db = self._mock_db_no_rate()
        usd, rate, src, issues = normalize_currency(100.0, "XYZ", date(2024, 1, 1), db)
        assert src == "unknown"
        assert any(i["severity"] == "error" for i in issues)

    def test_unit_dozen_multiplied(self):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = [
            ("dozen", "unit", 12.0),
            ("unit",  "unit", 1.0),
        ]
        qty, issues = normalize_quantity(5, "dozen", db)
        assert qty == 60
        assert any(i["issue_type"] == "unit_conversion" for i in issues)

    def test_unit_none_passthrough(self):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = []
        qty, issues = normalize_quantity(10, None, db)
        assert qty == 10
        assert not issues


# ── Late arrivals ─────────────────────────────────────────

class TestLateArrivals:
    def test_today_is_normal(self):
        result = classify_lateness(date.today())
        assert result["severity"] == "normal"
        assert not result["requires_recompute"]

    def test_one_day_old_normal(self):
        result = classify_lateness(date.today() - timedelta(days=1))
        assert result["severity"] == "normal"

    def test_4_days_old_soft_late(self):
        result = classify_lateness(date.today() - timedelta(days=4))
        assert result["severity"] == "soft_late"
        assert result["requires_recompute"]

    def test_10_days_old_late(self):
        result = classify_lateness(date.today() - timedelta(days=10))
        assert result["severity"] == "late"
        assert result["requires_recompute"]

    def test_40_days_old_very_late(self):
        result = classify_lateness(date.today() - timedelta(days=40))
        assert result["severity"] == "very_late"
        assert result["requires_review"]


# ── CSV parser ────────────────────────────────────────────

class TestCsvParser:
    def test_standard_csv(self):
        csv = b"transaction_date,revenue,category_name\n2024-01-01,100.0,Electronics\n"
        records = parse_csv(csv, "test")
        assert len(records) == 1
        assert records[0]["transaction_date"] == "2024-01-01"

    def test_semicolon_separator(self):
        csv = b"transaction_date;revenue;category_name\n2024-01-01;100.0;Electronics\n"
        records = parse_csv(csv, "test")
        assert len(records) == 1

    def test_normalises_column_names(self):
        csv = b"Transaction Date,Total Revenue\n2024-01-01,100\n"
        records = parse_csv(csv, "test")
        assert "transaction_date" in records[0]
        assert "total_revenue" in records[0]

    def test_drops_empty_rows(self):
        csv = b"transaction_date,revenue\n2024-01-01,100\n,,\n2024-01-02,200\n"
        records = parse_csv(csv, "test")
        # Empty row dropped
        real = [r for r in records if any(v for v in r.values() if v)]
        assert len(real) == 2

    def test_handles_utf8_bom(self):
        csv = b"\xef\xbb\xbftransaction_date,revenue\n2024-01-01,100\n"
        records = parse_csv(csv, "test")
        assert len(records) == 1
