# ADR 003 — Forecast fallback chain for segments without a trained model

**Status:** Accepted  
**Date:** 2026-06-22  
**Deciders:** Engineering team  

---

## Context

The platform trains one model per segment, where a segment is a `(category_id, region_id)` combination. With 5 categories and 5 regions there are 25 specific segments plus a global aggregate — 26 total.

When a user selects a specific category and region in the UI and clicks "Generate forecast", the predictor calls `get_deployed_model_info(db, category_id, region_id)`. In the original implementation this function returned `None` if no model was deployed for that exact segment, and `predict_segment` returned a `no_model` error.

This caused a poor user experience: training succeeded (the global model was deployed) but clicking "Generate forecast" for any specific category/region filter showed "No deployed model found. Run training first." even though a perfectly applicable model existed.

The root cause was that specific segments often have fewer than 30 days of data in the training window — they were skipped during training. But the global model, trained on all 11,250 records aggregated by date, was always successfully trained.

---

## Decision

Implement a four-level fallback chain in `get_deployed_model_info`:

1. **Exact match** — `segment_key(category_id, region_id)` e.g. `"cat=2|region=3"`
2. **Same category, all regions** — `segment_key(category_id, None)` e.g. `"cat=2|region=all"`
3. **All categories, same region** — `segment_key(None, region_id)` e.g. `"cat=all|region=3"`
4. **Global model** — `"global"`
5. **Any deployed model** — last resort, picks the model with lowest MAPE across all segments

When a fallback model is used, `predict_segment` checks whether the requested segment has enough data (≥30 rows) to re-train the model on. If it does, the model is re-trained on the requested segment's data. If it doesn't (sparse segment), the broader segment's data is used for training but the forecast is written under the requested segment key so the UI can find it.

Additionally, `train_segment` was updated with a matching fallback: if a specific segment has fewer than `MIN_TRAIN_ROWS` rows, training falls back to the category-wide aggregate, then the global aggregate, but registers the model under the original requested segment key.

---

## Alternatives considered

| Option | Outcome |
|---|---|
| Require the user to train with no filter first | Forces a two-step workflow that is non-obvious; users expect "train → forecast" to work regardless of filter |
| Lower MIN_TRAIN_ROWS to 1 | Produces unreliable models on very sparse data; 30 rows is already a pragmatic minimum |
| Pre-train all 26 segments on first boot | Adds 2–3 minutes to startup time; still fails for segments that genuinely have no data |
| Use the global model's forecast for all segments | Ignores segment-specific patterns that do exist in the data |

---

## Consequences

**Positive:**
- "Train then forecast" works correctly for any filter combination the user selects
- Users never see a confusing "no model" error after a successful training run
- The fallback is transparent — the forecast chart appears as expected

**Negative:**
- A forecast for `cat=Dairy|region=Asia Pacific` might actually be generated from the global model if that specific combination has sparse data — the segment label in the UI is accurate but the underlying model scope is broader
- The `forecast_results` table stores forecasts under the requested segment key regardless of which model generated them — tracing back which model produced a specific forecast requires joining on `model_id`

**Future work:**
- Add a tooltip or badge in the Forecast Explorer indicating when a fallback model was used ("Using global model")
- Train per-category models (collapsing the region dimension) as an intermediate tier between global and fully-specific
