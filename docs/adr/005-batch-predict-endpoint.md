# ADR 005 — Use /predict/batch instead of /predict for the "Generate forecast" button

**Status:** Accepted  
**Date:** 2026-06-22  
**Deciders:** Engineering team  

---

## Context

The forecasting API exposes two prediction endpoints:

- `POST /api/v1/forecasting/predict` — generates a forecast for one specific `(category_id, region_id)` segment, requires an exact deployed model for that segment
- `POST /api/v1/forecasting/predict/batch` — iterates all segments (optionally filtered by `category_ids` and `region_ids`), uses the fallback chain to find a model for each segment, writes all results to `forecast_results`

The frontend "Generate forecast" button originally called `/predict` (single-segment). This worked for the global view (no filter) but failed for any specific category/region selection because:

1. `/predict` looks up the model using the exact `segment_key` 
2. Specific segments rarely have their own trained model (sparse data → skipped during training)
3. Without a fallback, `/predict` returned HTTP 404 with `"No deployed model found. Run training first."`

The fallback chain (ADR 003) was added to `get_deployed_model_info` but the single-segment endpoint still had a secondary failure mode: even with a fallback model found, it would re-train on the sparse segment's data and produce an unreliable forecast.

---

## Decision

Change the "Generate forecast" button to call `/predict/batch` with optional `category_ids` and `region_ids` filters matching the current UI filter state.

```javascript
// Before
triggerPrediction({ category_id: categoryId, region_id: regionId, horizon_days: 30 })

// After  
triggerPrediction({
  category_ids:  categoryId ? [categoryId] : undefined,
  region_ids:    regionId   ? [regionId]   : undefined,
  horizon_days:  30,
})
```

`/predict/batch` applies the same filter logic as the training pipeline — it processes the global segment plus all matching specific segments. For each segment it applies the fallback chain and writes the forecast under the requested segment key. The UI then retrieves forecasts via `GET /forecasts?category_id=X&region_id=Y` which finds the written row.

---

## Consequences

**Positive:**
- A single "Generate forecast" click produces forecasts for all filtered segments simultaneously
- The batch endpoint's response includes `segmentsForecast`, `segmentsNoModel`, and `segmentsFailed` counters — the frontend can show a meaningful summary
- Consistent with how training works (also batch-oriented)

**Negative:**
- Batch predict for all 26 segments takes longer than a single-segment predict (~30 seconds vs ~2 seconds for the global segment alone)
- The batch endpoint's `run_sync=true` blocks the request for the full duration — for large segment counts this should be moved to a background task with polling

**Future work:**
- Add a progress indicator or websocket for long-running batch predictions
- Allow the UI to trigger async batch predict and poll for completion via `GET /forecasting/runs/{run_id}`
