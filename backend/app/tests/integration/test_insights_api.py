"""
Phase 3 integration tests — AI Insights API.
DeepSeek calls are mocked; the real test database is used for context.
"""

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from app.tests.conftest import seed_category, seed_region


def _seed_revenue(db, n_days: int = 90):
    """Seed agg_revenue_daily with synthetic data for insight tests."""
    import numpy as np
    cat_id = seed_category(db, "Dairy")
    reg_id = seed_region(db, "North America")

    today = date.today()
    for i in range(n_days, 0, -1):
        d   = today - timedelta(days=i)
        rev = 4000 + 800 * np.sin(i * 2 * 3.14159 / 365) + np.random.randn() * 150
        db.execute(text("""
            INSERT INTO agg_revenue_daily (agg_date, category_id, region_id, total_revenue, total_quantity, txn_count)
            VALUES (:d, :cat, :reg, :rev, 80, 8) ON CONFLICT DO NOTHING
        """), {"d": d, "cat": cat_id, "reg": reg_id, "rev": max(rev, 100)})
    db.commit()
    return cat_id, reg_id


MOCK_TEXT = "Revenue grew 9% YoY driven by Dairy in North America.\nCONFIDENCE: 0.88"
MOCK_RESPONSE = MagicMock(
    text=MOCK_TEXT,
    model="deepseek-chat",
    tokens_prompt=350,
    tokens_completion=110,
    tokens_total=460,
    latency_ms=900,
    finish_reason="stop",
)


# ── 1. Trend Summarization ────────────────────────────────

class TestTrendEndpoint:
    @patch("app.insights.engines.insight_engines.get_llm_client")
    def test_trend_summary(self, mock_get_client, client, db):
        _seed_revenue(db)
        mock_get_client.return_value.complete = AsyncMock(return_value=MOCK_RESPONSE)

        r = client.post("/api/v1/insights/trend", json={"lookback_days": 30})
        assert r.status_code == 200
        data = r.json()
        assert data["insightType"] == "trend"
        assert data["confidence"]  > 0
        assert "CONFIDENCE" not in data["insightText"]

    @patch("app.insights.engines.insight_engines.get_llm_client")
    def test_trend_with_segment(self, mock_get_client, client, db):
        cat_id, reg_id = _seed_revenue(db)
        mock_get_client.return_value.complete = AsyncMock(return_value=MOCK_RESPONSE)

        r = client.post("/api/v1/insights/trend", json={
            "categoryId": cat_id, "regionId": reg_id, "lookbackDays": 60,
        })
        assert r.status_code == 200


# ── 2. Root Cause Analysis ────────────────────────────────

class TestRootCauseEndpoint:
    @patch("app.insights.engines.insight_engines.get_llm_client")
    def test_root_cause(self, mock_get_client, client, db):
        _seed_revenue(db)
        mock_get_client.return_value.complete = AsyncMock(return_value=MagicMock(
            text="PRIMARY DRIVER: x\nCONTRIBUTING FACTORS: y\nEVIDENCE: z\nDATA GAPS: none\nCONFIDENCE: 0.82",
            model="deepseek-chat", tokens_prompt=400, tokens_completion=200,
            tokens_total=600, latency_ms=1100, finish_reason="stop",
        ))

        r = client.post("/api/v1/insights/root-cause", json={
            "changeDescription": "Revenue dropped 15% in the last two weeks",
        })
        assert r.status_code == 200
        assert r.json()["insightType"] == "root_cause"

    def test_root_cause_validates_min_length(self, client):
        r = client.post("/api/v1/insights/root-cause", json={"changeDescription": "short"})
        assert r.status_code == 422


# ── 3. Forecast Explanation ───────────────────────────────

class TestForecastExplainEndpoint:
    @patch("app.insights.engines.insight_engines.get_llm_client")
    def test_forecast_explain_no_model(self, mock_get_client, client, db):
        """No deployed model exists — engine should still respond gracefully."""
        _seed_revenue(db)
        mock_get_client.return_value.complete = AsyncMock(return_value=MOCK_RESPONSE)

        r = client.post("/api/v1/insights/forecast/explain", json={"horizonDays": 14})
        assert r.status_code == 200


# ── 4. Revenue Driver Analysis ────────────────────────────

class TestDriverEndpoint:
    @patch("app.insights.engines.insight_engines.get_llm_client")
    def test_drivers(self, mock_get_client, client, db):
        _seed_revenue(db)
        mock_get_client.return_value.complete = AsyncMock(return_value=MagicMock(
            text="1. Dairy — $50,000 (40% of total) — growing — strong seasonal demand\nCONFIDENCE: 0.85",
            model="deepseek-chat", tokens_prompt=300, tokens_completion=150,
            tokens_total=450, latency_ms=950, finish_reason="stop",
        ))

        r = client.post("/api/v1/insights/drivers", json={"lookbackDays": 60})
        assert r.status_code == 200
        assert r.json()["insightType"] == "driver"


# ── 5. Executive Summary ──────────────────────────────────

class TestExecutiveSummaryEndpoint:
    @patch("app.insights.engines.insight_engines.get_llm_client")
    def test_executive_summary(self, mock_get_client, client, db):
        _seed_revenue(db)
        mock_get_client.return_value.complete = AsyncMock(return_value=MagicMock(
            text=(
                "PERFORMANCE HEADLINE: Revenue reached $120,000.\n"
                "WHAT HAPPENED: Steady growth.\n"
                "FORECAST: Stable outlook.\n"
                "TOP RISK: Seasonal dip.\n"
                "TOP OPPORTUNITY: Expand Dairy.\n"
                "RECOMMENDED ACTION: Increase Dairy marketing spend.\n"
                "CONFIDENCE: 0.80"
            ),
            model="deepseek-chat", tokens_prompt=500, tokens_completion=250,
            tokens_total=750, latency_ms=1300, finish_reason="stop",
        ))

        r = client.post("/api/v1/insights/executive-summary", json={
            "lookbackDays": 90, "horizonDays": 30,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["insightType"] == "executive"
        assert "PERFORMANCE HEADLINE" in data["insightText"]


# ── Cache behavior ─────────────────────────────────────────

class TestInsightCache:
    @patch("app.insights.engines.insight_engines.get_llm_client")
    def test_second_call_uses_cache(self, mock_get_client, client, db):
        _seed_revenue(db)
        mock_get_client.return_value.complete = AsyncMock(return_value=MOCK_RESPONSE)

        payload = {"lookbackDays": 45}
        r1 = client.post("/api/v1/insights/trend", json=payload)
        r2 = client.post("/api/v1/insights/trend", json=payload)

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r2.json()["fromCache"] is True
        # LLM invoked exactly once across both calls
        assert mock_get_client.return_value.complete.call_count == 1

    @patch("app.insights.engines.insight_engines.get_llm_client")
    def test_different_params_bypass_cache(self, mock_get_client, client, db):
        _seed_revenue(db)
        mock_get_client.return_value.complete = AsyncMock(return_value=MOCK_RESPONSE)

        client.post("/api/v1/insights/trend", json={"lookbackDays": 30})
        client.post("/api/v1/insights/trend", json={"lookbackDays": 90})

        assert mock_get_client.return_value.complete.call_count == 2


# ── Audit log ─────────────────────────────────────────────

class TestInsightLog:
    def test_log_empty_initially(self, client):
        r = client.get("/api/v1/insights/log")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @patch("app.insights.engines.insight_engines.get_llm_client")
    def test_log_populated_after_call(self, mock_get_client, client, db):
        _seed_revenue(db)
        mock_get_client.return_value.complete = AsyncMock(return_value=MOCK_RESPONSE)

        client.post("/api/v1/insights/trend", json={"lookbackDays": 30})

        r = client.get("/api/v1/insights/log?insight_type=trend")
        assert r.status_code == 200
        assert len(r.json()) >= 1

    @patch("app.insights.engines.insight_engines.get_llm_client")
    def test_log_filter_by_type(self, mock_get_client, client, db):
        _seed_revenue(db)
        mock_get_client.return_value.complete = AsyncMock(return_value=MOCK_RESPONSE)

        client.post("/api/v1/insights/trend", json={"lookbackDays": 30})

        r = client.get("/api/v1/insights/log?insight_type=driver")
        assert r.status_code == 200
        assert all(row["insightType"] == "driver" for row in r.json())
