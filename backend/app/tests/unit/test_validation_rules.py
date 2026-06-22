"""
Unit tests for all validation rules.
No database required — rules operate on plain dicts.
"""

from datetime import date, timedelta

import pytest

from app.validation.engine import RuleEngine, RuleOutcome
from app.validation.rules import (
    CurrencyValidationRule,
    DateValidationRule,
    PlausibilityRule,
    QuantityValidationRule,
    RequiredFieldsRule,
    RevenueValidationRule,
    SkuFormatRule,
    StringSanitisationRule,
    get_standard_rules,
)


# ── RequiredFieldsRule ────────────────────────────────────

class TestRequiredFieldsRule:
    rule = RequiredFieldsRule()

    def test_passes_with_all_required(self):
        rec = {"transaction_date": "2024-01-15", "revenue": 100.0,
               "category_name": "Electronics", "region_name": "North America"}
        assert self.rule.validate(rec).is_ok

    def test_rejects_missing_transaction_date(self):
        result = self.rule.validate({"revenue": 100.0})
        assert result.is_rejected
        assert "transaction_date" in result.issue_detail

    def test_rejects_missing_revenue(self):
        result = self.rule.validate({"transaction_date": "2024-01-01"})
        assert result.is_rejected
        assert "revenue" in result.issue_detail

    def test_warns_missing_category(self):
        result = self.rule.validate({"transaction_date": "2024-01-01", "revenue": 50.0})
        assert result.outcome == RuleOutcome.WARN
        assert result.corrected_record["category_name"] == "Unknown"

    def test_rejects_empty_string_date(self):
        result = self.rule.validate({"transaction_date": "", "revenue": 100.0})
        assert result.is_rejected


# ── DateValidationRule ────────────────────────────────────

class TestDateValidationRule:
    rule = DateValidationRule()

    def test_passes_iso_date(self):
        result = self.rule.validate({"transaction_date": date(2024, 1, 15)})
        assert result.is_ok

    def test_coerces_string_iso(self):
        result = self.rule.validate({"transaction_date": "2024-01-15"})
        assert not result.is_rejected
        assert result.corrected_record["transaction_date"] == date(2024, 1, 15)

    def test_coerces_uk_format(self):
        result = self.rule.validate({"transaction_date": "15/01/2024"})
        assert not result.is_rejected

    def test_rejects_gibberish(self):
        result = self.rule.validate({"transaction_date": "not-a-date"})
        assert result.is_rejected

    def test_warns_future_date(self):
        future = str(date.today() + timedelta(days=10))
        result = self.rule.validate({"transaction_date": future})
        assert result.outcome == RuleOutcome.WARN
        assert result.corrected_record["transaction_date"] == date.today()

    def test_warns_very_old_date(self):
        ancient = "2010-01-01"
        result = self.rule.validate({"transaction_date": ancient})
        assert result.outcome == RuleOutcome.WARN

    def test_coerces_timestamp(self):
        result = self.rule.validate({"transaction_date": "2024-03-15T14:30:00"})
        assert not result.is_rejected
        assert result.corrected_record["transaction_date"] == date(2024, 3, 15)


# ── RevenueValidationRule ─────────────────────────────────

class TestRevenueValidationRule:
    rule = RevenueValidationRule()

    def test_passes_positive_float(self):
        assert self.rule.validate({"revenue": 150.75}).is_ok

    def test_coerces_string_number(self):
        result = self.rule.validate({"revenue": "150.75"})
        assert not result.is_rejected

    def test_coerces_currency_string(self):
        result = self.rule.validate({"revenue": "$1,250.00"})
        assert not result.is_rejected
        assert result.corrected_record["revenue"] == 1250.0

    def test_warns_negative(self):
        result = self.rule.validate({"revenue": -50.0})
        assert result.outcome == RuleOutcome.WARN

    def test_warns_zero(self):
        result = self.rule.validate({"revenue": 0})
        assert result.outcome == RuleOutcome.WARN

    def test_rejects_non_numeric(self):
        result = self.rule.validate({"revenue": "N/A"})
        assert result.is_rejected


# ── QuantityValidationRule ────────────────────────────────

class TestQuantityValidationRule:
    rule = QuantityValidationRule()

    def test_passes_positive_int(self):
        assert self.rule.validate({"quantity": 5}).is_ok

    def test_defaults_missing_to_one(self):
        result = self.rule.validate({})
        assert not result.is_rejected
        assert result.corrected_record["quantity"] == 1

    def test_coerces_float_string(self):
        result = self.rule.validate({"quantity": "3.0"})
        assert result.corrected_record["quantity"] == 3

    def test_warns_zero_quantity(self):
        result = self.rule.validate({"quantity": 0})
        assert result.outcome == RuleOutcome.WARN
        assert result.corrected_record["quantity"] == 1


# ── CurrencyValidationRule ────────────────────────────────

class TestCurrencyValidationRule:
    rule = CurrencyValidationRule()

    def test_passes_usd(self):
        assert self.rule.validate({"currency": "USD"}).is_ok

    def test_uppercases_lowercase(self):
        result = self.rule.validate({"currency": "eur"})
        assert result.corrected_record["currency"] == "EUR"

    def test_warns_unknown_currency(self):
        result = self.rule.validate({"currency": "XYZ"})
        assert result.outcome == RuleOutcome.WARN

    def test_defaults_none_to_usd(self):
        result = self.rule.validate({"currency": None})
        assert result.corrected_record["currency"] == "USD"


# ── PlausibilityRule ──────────────────────────────────────

class TestPlausibilityRule:
    rule = PlausibilityRule()

    def test_passes_normal_unit_price(self):
        assert self.rule.validate({"revenue": 500.0, "quantity": 10}).is_ok

    def test_warns_very_high_unit_price(self):
        result = self.rule.validate({"revenue": 1_000_000.0, "quantity": 1})
        assert result.outcome == RuleOutcome.WARN

    def test_passes_with_missing_fields(self):
        assert self.rule.validate({}).is_ok


# ── SkuFormatRule ─────────────────────────────────────────

class TestSkuFormatRule:
    rule = SkuFormatRule()

    def test_passes_normal_sku(self):
        assert self.rule.validate({"sku_id": "ELEC-001"}).is_ok

    def test_truncates_long_sku(self):
        long_sku = "A" * 100
        result = self.rule.validate({"sku_id": long_sku})
        assert result.outcome == RuleOutcome.WARN
        assert len(result.corrected_record["sku_id"]) == 80

    def test_warns_suspicious_chars(self):
        result = self.rule.validate({"sku_id": "<script>alert(1)</script>"})
        assert result.outcome == RuleOutcome.WARN

    def test_passes_no_sku(self):
        assert self.rule.validate({}).is_ok


# ── StringSanitisationRule ────────────────────────────────

class TestStringSanitisationRule:
    rule = StringSanitisationRule()

    def test_strips_whitespace(self):
        result = self.rule.validate({"category_name": "  Electronics  "})
        assert result.corrected_record["category_name"] == "Electronics"

    def test_title_cases_category(self):
        result = self.rule.validate({"category_name": "home & garden"})
        assert result.corrected_record["category_name"] == "Home & Garden"

    def test_passes_already_clean(self):
        assert self.rule.validate({"category_name": "Electronics"}).is_ok


# ── RuleEngine integration ────────────────────────────────

class TestRuleEngine:
    def test_full_valid_record_passes(self):
        engine = RuleEngine(rules=get_standard_rules(), source_name="test")
        record = {
            "transaction_date": "2024-06-15",
            "category_name":    "Electronics",
            "region_name":      "North America",
            "revenue":          "£250.00",
            "quantity":         "5",
            "currency":         "GBP",
        }
        result = engine.run(record)
        assert result.accepted

    def test_missing_date_rejected(self):
        engine = RuleEngine(rules=get_standard_rules(), source_name="test")
        result = engine.run({"revenue": 100.0})
        assert not result.accepted
        assert len(result.rejection_reasons) > 0

    def test_batch_separates_valid_invalid(self):
        engine = RuleEngine(rules=get_standard_rules(), source_name="test")
        records = [
            {"transaction_date": "2024-01-15", "revenue": 100.0,
             "category_name": "Electronics", "region_name": "NA"},
            {"revenue": 50.0},  # missing date — rejected
        ]
        accepted, rejected, issues = engine.run_batch(records)
        assert len(accepted) == 1
        assert len(rejected) == 1

    def test_auto_corrections_applied(self):
        engine = RuleEngine(rules=get_standard_rules(), source_name="test")
        record = {
            "transaction_date": "15/01/2024",   # non-ISO date
            "revenue":          "$500",          # currency symbol
            "quantity":         None,            # missing
            "category_name":    "  electronics  ",  # untrimmed
        }
        result = engine.run(record)
        assert result.accepted
        assert result.record["transaction_date"] == date(2024, 1, 15)
        assert result.record["revenue"] == 500.0
        assert result.record["quantity"] == 1
        assert result.record["category_name"] == "Electronics"
