#!/usr/bin/env python3
"""
Synthetic historical data generator for the CPG Predictive Intelligence Platform.

Usage:
    python generate_synthetic_data.py
    python generate_synthetic_data.py --days 450 --train --forecast
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import time
from datetime import date, timedelta

import httpx

CATEGORIES = {
    "Snacks":       {"base_daily": 4200, "seasonality": "summer_holiday"},
    "Beverages":    {"base_daily": 5100, "seasonality": "summer"},
    "Dairy":        {"base_daily": 3600, "seasonality": "flat"},
    "Frozen Foods": {"base_daily": 2900, "seasonality": "winter"},
    "Household":    {"base_daily": 3300, "seasonality": "flat"},
}

REGIONS = {
    "North America": 1.35,
    "Europe":        1.10,
    "Asia Pacific":  0.95,
    "Latin America": 0.65,
    "Middle East":   0.55,
}

BATCH_SIZE = 1000


def seasonal_multiplier(category_name: str, d: date) -> float:
    profile = CATEGORIES[category_name]["seasonality"]
    angle = 2 * math.pi * d.timetuple().tm_yday / 365.25
    if profile == "summer":
        return 1.0 + 0.28 * math.sin(angle - math.pi / 2)
    if profile == "summer_holiday":
        return 1.0 + 0.18 * math.sin(angle - math.pi / 2) + (0.35 if d.month == 12 and d.day >= 10 else 0.0)
    if profile == "winter":
        return 1.0 + 0.22 * math.sin(angle + math.pi / 2)
    return 1.0


def weekly_multiplier(d: date) -> float:
    return [0.92, 0.90, 0.95, 1.00, 1.12, 1.22, 1.08][d.weekday()]


def growth_multiplier(d: date, start: date, end: date) -> float:
    return 1.0 + 0.18 * (d - start).days / max((end - start).days, 1)


def dairy_decline_multiplier(category_name: str, d: date, end: date) -> float:
    if category_name != "Dairy":
        return 1.0
    days_from_end = (end - d).days
    if days_from_end > 21:
        return 1.0
    return 1.0 - 0.22 * (21 - days_from_end) / 21


def promo_spike(d: date, category_name: str, region_name: str) -> float:
    local_rng = random.Random(f"{d.isoformat()}|{category_name}|{region_name}")
    if local_rng.random() < 0.015:
        return local_rng.uniform(1.4, 1.9)
    return 1.0


def generate_day_records(d: date, start: date, end: date, rng: random.Random) -> list[dict]:
    records = []
    for category_name, cfg in CATEGORIES.items():
        for region_name, region_scale in REGIONS.items():
            base = cfg["base_daily"] * region_scale
            mult = (
                seasonal_multiplier(category_name, d)
                * weekly_multiplier(d)
                * growth_multiplier(d, start, end)
                * dairy_decline_multiplier(category_name, d, end)
                * promo_spike(d, category_name, region_name)
            )
            revenue  = max(round(base * mult * rng.uniform(0.90, 1.10), 2), 50.0)
            quantity = max(1, round(revenue / rng.uniform(3.5, 9.0)))
            records.append({
                "transaction_date": d.isoformat(),
                "category_name":    category_name,
                "region_name":      region_name,
                "revenue":          revenue,
                "quantity":         quantity,
                "currency":         "USD",
            })
    return records


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic CPG historical data")
    parser.add_argument("--api-url",      default="http://127.0.0.1:8000")
    parser.add_argument("--days",         type=int, default=450)
    parser.add_argument("--seed",         type=int, default=42)
    parser.add_argument("--train",        action="store_true")
    parser.add_argument("--forecast",     action="store_true")
    parser.add_argument("--horizon-days", type=int, default=30)
    args = parser.parse_args()

    rng   = random.Random(args.seed)
    end   = date.today() - timedelta(days=1)
    start = end - timedelta(days=args.days - 1)

    print(f"Target API: {args.api_url}")
    print(f"Generating {args.days} days: {start} → {end}")
    print()

    print("Checking API connectivity...")
    try:
        httpx.get(f"{args.api_url}/api/v1/health", timeout=5).raise_for_status()
        print("  OK")
    except Exception as exc:
        print(f"  FAILED -- {exc}", file=sys.stderr)
        sys.exit(1)
    print()

    # Generate all records
    all_records: list[dict] = []
    d = start
    while d <= end:
        all_records.extend(generate_day_records(d, start, end, rng))
        d += timedelta(days=1)

    total = len(all_records)
    print(f"Generated {total:,} records "
          f"({len(CATEGORIES)} categories × {len(REGIONS)} regions × {args.days} days)")
    print()

    # Push in batches
    accepted = rejected = duplicates = 0
    total_batches = -(-total // BATCH_SIZE)
    t0 = time.monotonic()

    with httpx.Client() as client:
        for i in range(0, total, BATCH_SIZE):
            batch     = all_records[i: i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            try:
                resp = client.post(
                    f"{args.api_url}/api/v1/ingestion/push",
                    json={"records": batch, "sourceName": "synthetic_history"},
                    timeout=120,
                )
                resp.raise_for_status()
                pr = resp.json().get("pipelineResult", {})
                accepted   += pr.get("accepted", 0)
                rejected   += pr.get("rejected", 0)
                duplicates += pr.get("duplicatesSkipped", 0)
                print(f"  Batch {batch_num}/{total_batches}: "
                      f"{pr.get('accepted', 0)} accepted, "
                      f"{pr.get('rejected', 0)} rejected, "
                      f"{pr.get('duplicatesSkipped', 0)} duplicates")
            except httpx.HTTPStatusError as exc:
                print(f"  Batch {batch_num}/{total_batches}: FAILED -- {exc}", file=sys.stderr)
                sys.exit(1)

    print()
    print(f"Done in {time.monotonic() - t0:.1f}s — "
          f"{accepted:,} accepted, {rejected:,} rejected, {duplicates:,} duplicates")

    if args.train:
        print()
        print("Triggering model training (this may take a few minutes)...")
        resp = httpx.post(
            f"{args.api_url}/api/v1/forecasting/train",
            params={"run_sync": "true"},
            json={"modelNames": ["lightgbm"], "horizonDays": args.horizon_days},
            timeout=900,
        )
        resp.raise_for_status()
        r = resp.json()
        print(f"  Training complete: {r.get('segmentsTrained', 0)} segments trained, "
              f"avg MAPE {r.get('avgMape', 'n/a')}")

    if args.forecast:
        print()
        print("Generating forecasts...")
        resp = httpx.post(
            f"{args.api_url}/api/v1/forecasting/predict/batch",
            params={"run_sync": "true"},
            json={"horizonDays": args.horizon_days},
            timeout=600,
        )
        resp.raise_for_status()
        r = resp.json()
        print(f"  Forecast complete: {r.get('segmentsForecast', 0)} segments forecasted")

    print()
    print("Data load complete. Open the dashboard:")
    print("  http://localhost:5173")


if __name__ == "__main__":
    main()
