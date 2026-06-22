"""
Phase 3 unit tests — no DeepSeek calls made (pure logic).
"""

from datetime import date

import pytest

from app.insights.cache.insight_cache import _make_key
from app.insights.engines.insight_engines import InsightResult, _extract_confidence


# ── _extract_confidence ───────────────────────────────────

class TestExtractConfidence:
    def test_extracts_valid_score(self):
        text = "Revenue grew 12% YoY.\nCONFIDENCE: 0.87"
        clean, score = _extract_confidence(text)
        assert score == pytest.approx(0.87)
        assert "CONFIDENCE" not in clean

    def test_clamps_above_one(self):
        _, score = _extract_confidence("text\nCONFIDENCE: 1.50")
        assert score == 1.0

    def test_clamps_below_zero(self):
        _, score = _extract_confidence("text\nCONFIDENCE: -0.10")
        assert score == 0.0

    def test_returns_default_when_missing(self):
        clean, score = _extract_confidence("No confidence line here.")
        assert score == 0.75
        assert clean == "No confidence line here."

    def test_case_insensitive(self):
        _, score = _extract_confidence("text\nconfidence: 0.65")
        assert score == pytest.approx(0.65)

    def test_multiline_body_preserved(self):
        text = "PRIMARY DRIVER: x\nCONTRIBUTING FACTORS: y\nCONFIDENCE: 0.91"
        clean, score = _extract_confidence(text)
        assert "PRIMARY DRIVER" in clean
        assert "CONTRIBUTING FACTORS" in clean
        assert score == pytest.approx(0.91)


# ── Cache key ─────────────────────────────────────────────

class TestCacheKey:
    def test_same_inputs_same_key(self):
        k1 = _make_key("trend", "global", {"lookback_days": 90})
        k2 = _make_key("trend", "global", {"lookback_days": 90})
        assert k1 == k2

    def test_different_type_different_key(self):
        k1 = _make_key("trend",    "global", {"lookback_days": 90})
        k2 = _make_key("forecast", "global", {"lookback_days": 90})
        assert k1 != k2

    def test_different_params_different_key(self):
        k1 = _make_key("trend", "global", {"lookback_days": 90})
        k2 = _make_key("trend", "global", {"lookback_days": 30})
        assert k1 != k2

    def test_different_segment_different_key(self):
        k1 = _make_key("trend", "cat=1|region=2", {})
        k2 = _make_key("trend", "cat=1|region=3", {})
        assert k1 != k2

    def test_key_is_64_char_hex(self):
        k = _make_key("trend", "global", {})
        assert len(k) == 64
        int(k, 16)  # must be valid hex


# ── InsightResult ─────────────────────────────────────────

class TestInsightResult:
    def test_to_dict_contains_all_fields(self):
        r = InsightResult(
            insight_type="trend",
            insight_text="Revenue grew 8% YoY.",
            confidence=0.88,
            structured_data={"segment_key": "global"},
            model_used="deepseek-chat",
            tokens_total=250,
            latency_ms=1200,
        )
        d = r.to_dict()
        assert d["insight_type"] == "trend"
        assert d["confidence"]   == pytest.approx(0.88)
        assert d["tokens_total"] == 250
        assert d["model_used"]   == "deepseek-chat"
        assert d["from_cache"]   is False

    def test_from_cache_flag(self):
        r = InsightResult(insight_type="driver", insight_text="x", confidence=0.7, from_cache=True)
        assert r.to_dict()["from_cache"] is True

    def test_default_structured_data_empty_dict(self):
        r = InsightResult(insight_type="executive", insight_text="x", confidence=0.8)
        assert r.structured_data == {}
