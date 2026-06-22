# ADR 004 — Always refresh agg_revenue_daily after ingestion, not only on late arrivals

**Status:** Accepted  
**Date:** 2026-06-22  
**Deciders:** Engineering team  

---

## Context

`agg_revenue_daily` is the materialised view that the ML pipeline reads for training and prediction. It is populated by the PostgreSQL function `refresh_agg_revenue_daily(p_since DATE)` which deletes rows from `p_since` forward and re-inserts them from `fact_transactions`.

The original pipeline orchestrator called `refresh_agg_revenue_daily` only inside the late-arrivals branch:

```python
all_affected = list(set(affected_dates + late_dates))
if all_affected:
    recompute_result = late_arrivals.recompute_aggregates(db, all_affected)
```

This worked correctly on the first data load (all records were new, `affected_dates` was non-empty). It broke on every subsequent run where all records were duplicates (fingerprints already in `ingestion_fingerprints`): `affected_dates` was empty, `recompute_aggregates` was never called, and `agg_revenue_daily` was never refreshed.

The symptom was subtle: `generate_synthetic_data.py` reported "0 duplicates, 11250 accepted" on first run and appeared successful. On second run after a DB reset it reported "11250 duplicates, 0 accepted" — which was correct — but `agg_revenue_daily` remained empty, so `get_segments()` returned only `[(None, None)]` (global), and training produced "1 segment trained, 25 skipped".

---

## Decision

Add an unconditional `refresh_agg_revenue_daily(NULL)` call in the `else` branch (when `all_affected` is empty):

```python
all_affected = list(set(affected_dates + late_dates))
if all_affected:
    recompute_result = late_arrivals.recompute_aggregates(db, all_affected)
    result.recompute_triggered = recompute_result.get("recomputed", False)
else:
    # No new records this batch — still ensure the aggregate table is
    # populated from whatever is already in fact_transactions.
    try:
        db.execute(text("SELECT refresh_agg_revenue_daily(NULL)"))
        db.commit()
    except Exception:
        pass  # best-effort; don't fail the pipeline over an aggregate refresh
```

The `NULL` argument causes the function to rebuild the entire table from scratch, which is safe and idempotent. The call is wrapped in a try/except so that a refresh failure (e.g. the function doesn't exist yet on a fresh DB) does not cause the ingestion response to error — the ingestion itself succeeded; the aggregate is a derived view.

---

## Alternatives considered

| Option | Outcome |
|---|---|
| Always call refresh unconditionally (both branches) | Equivalent but slightly redundant — late-arrival branch already calls it via `recompute_aggregates` |
| Add a separate `/api/v1/admin/refresh-aggregates` endpoint | Requires manual operator action; easy to forget |
| Use a PostgreSQL trigger on `fact_transactions` | Triggers on high-volume inserts cause severe performance degradation; not appropriate for batch ingestion |
| Schedule a cron refresh every N minutes | Eventual consistency; training might run before the cron fires |

---

## Consequences

**Positive:**
- `agg_revenue_daily` is always consistent with `fact_transactions` after any ingestion run, regardless of whether new records were accepted or all were duplicates
- Re-running `generate_synthetic_data.py` after a DB reset correctly repopulates the aggregate table

**Negative:**
- When all records are duplicates, a full `refresh_agg_revenue_daily(NULL)` rebuild runs unnecessarily. For 11,250 records this takes ~50ms — acceptable. For tens of millions of records a smarter incremental strategy would be needed.

**Future work:**
- Track the last successful refresh timestamp and skip the unconditional refresh if it ran within the last N seconds
- Consider a partial refresh (`p_since = MIN(fact_transactions.transaction_date)`) instead of full rebuild for large tables
