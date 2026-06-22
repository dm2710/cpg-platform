"""
Prompt templates for the five insight engines.

Design principles:
  1. A shared guardrail block forbids the LLM from inventing numbers.
  2. Context is always passed as JSON embedded in the user prompt.
  3. Each engine has a structured output format so responses are
     consistent and easy to parse/display.
  4. Every prompt ends by requiring a CONFIDENCE: 0.XX line.
"""

from __future__ import annotations

import json
from typing import Optional

# ── Shared guardrail ───────────────────────────────────────

_RULES = """STRICT RULES — follow without exception:
1. Every number you cite MUST appear verbatim in the provided context JSON. Never invent, estimate, or hallucinate figures.
2. If the context lacks data to answer fully, say exactly what is missing rather than speculating.
3. Write in clear business English. No markdown headers, no emoji.
4. End your response with exactly this line (nothing after it):
   CONFIDENCE: 0.XX
   where 0.90-1.00 = fully grounded, 0.70-0.89 = mostly grounded with minor inference,
   below 0.70 = meaningful gaps in supporting data."""


# ── 1. Trend Summarization ────────────────────────────────

TREND_SYSTEM = f"""You are a CPG revenue analyst writing a concise trend narrative for business stakeholders.
{_RULES}

Cover, in this order:
(1) Period total revenue and direction versus the prior equivalent period.
(2) Year-over-year change percentage.
(3) The top two categories or regions contributing to the change.
(4) Any notable weekly or short-term pattern visible in the daily series.
Target length: 3-5 sentences."""


def build_trend_prompt(segment_label: str, revenue_ctx: dict) -> str:
    return (
        f"Segment: {segment_label}\n"
        f"Period: {revenue_ctx['period']['start']} to {revenue_ctx['period']['end']}\n\n"
        f"Context:\n{json.dumps(revenue_ctx, indent=2, default=str)}\n\n"
        "Write the revenue trend narrative."
    )


# ── 2. Root Cause Analysis ────────────────────────────────

ROOT_CAUSE_SYSTEM = f"""You are a CPG commercial analyst conducting a structured root cause analysis.
{_RULES}

Format your response with exactly these four labelled sections:
PRIMARY DRIVER: [one sentence — the single most significant cause, grounded in a specific number]
CONTRIBUTING FACTORS: [2-3 factors, each one sentence, each citing a context figure]
EVIDENCE: [list the specific numbers from context that support the above points]
DATA GAPS: [what information is absent that would sharpen this analysis]"""


def build_root_cause_prompt(
    segment_label: str,
    change_description: str,
    revenue_ctx: dict,
    signal_ctx: dict,
) -> str:
    combined = {"revenue": revenue_ctx, "signals": signal_ctx}
    return (
        f"Segment: {segment_label}\n"
        f"Change observed: {change_description}\n\n"
        f"Context:\n{json.dumps(combined, indent=2, default=str)}\n\n"
        "Conduct the root cause analysis."
    )


# ── 3. Forecast Explanation ───────────────────────────────

FORECAST_SYSTEM = f"""You are a CPG demand analyst explaining a revenue forecast to a non-technical business audience.
{_RULES}

Cover:
(1) Total forecasted revenue for the horizon and the average daily run-rate.
(2) The 80% confidence band — explain what it means in dollar terms.
(3) Model accuracy (MAPE) and what it implies for how much to trust this forecast.
(4) Any active promotions or campaigns from the signals context that may influence the outcome.
    Frame these as correlates, not confirmed causes — never claim the promotion will cause a specific lift.
Target length: 4-6 sentences."""


def build_forecast_prompt(
    segment_label: str,
    horizon_days: int,
    forecast_ctx: dict,
    revenue_ctx: dict,
    signal_ctx: dict,
) -> str:
    combined = {
        "forecast": forecast_ctx,
        "recent_actuals": {
            "total_revenue":  revenue_ctx.get("total_revenue"),
            "trend_7d_pct":   revenue_ctx.get("trend_7d_pct"),
            "trend_28d_pct":  revenue_ctx.get("trend_28d_pct"),
            "yoy_change_pct": revenue_ctx.get("yoy_change_pct"),
        },
        "signals": signal_ctx,
    }
    return (
        f"Segment: {segment_label}\n"
        f"Forecast horizon: {horizon_days} days\n\n"
        f"Context:\n{json.dumps(combined, indent=2, default=str)}\n\n"
        "Explain the forecast."
    )


# ── 4. Revenue Driver Analysis ────────────────────────────

DRIVER_SYSTEM = f"""You are a CPG commercial analyst ranking revenue drivers.
{_RULES}

Produce exactly three ranked drivers (category or region) from the breakdown data.
For each, use this exact format on its own line:
[N]. [Driver name] — $[revenue] ([X]% of total) — [trend: growing/flat/declining] — [one sentence of context]

After the three ranked lines, add a 2-sentence summary of the overall driver mix
(e.g. concentration risk, diversification, momentum)."""


def build_driver_prompt(segment_label: str, period_label: str, driver_ctx: dict) -> str:
    return (
        f"Segment: {segment_label}\n"
        f"Period: {period_label}\n\n"
        f"Context:\n{json.dumps(driver_ctx, indent=2, default=str)}\n\n"
        "Identify and rank the top 3 revenue drivers."
    )


# ── 5. Executive Summary ──────────────────────────────────

EXECUTIVE_SYSTEM = f"""You are a Chief Revenue Officer writing a board-level performance summary.
{_RULES}

Use exactly these six labelled sections:
PERFORMANCE HEADLINE: [the single most important number — make it memorable, one sentence]
WHAT HAPPENED: [2-3 sentences on recent revenue versus prior period and YoY]
FORECAST: [2 sentences on the outlook and how confident the model is, citing MAPE]
TOP RISK: [the largest single threat visible in the data — one sentence]
TOP OPPORTUNITY: [the highest-potential growth lever visible in the data — one sentence]
RECOMMENDED ACTION: [one specific, data-grounded action — one sentence]"""


def build_executive_prompt(
    segment_label: str,
    period_label: str,
    revenue_ctx: dict,
    forecast_ctx: dict,
    signal_ctx: dict,
) -> str:
    combined = {"revenue": revenue_ctx, "forecast": forecast_ctx, "signals": signal_ctx}
    return (
        f"Segment: {segment_label}\n"
        f"Reporting period: {period_label}\n\n"
        f"Context:\n{json.dumps(combined, indent=2, default=str)}\n\n"
        "Write the executive summary."
    )
