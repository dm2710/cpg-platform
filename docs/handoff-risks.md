# Handoff Risks — CPG Platform

This document captures known risks, gotchas, and technical debt items that the receiving engineering team needs to be aware of before taking ownership of this codebase.

---

## 🔴 Critical — must fix before any real deployment

### 1. Authentication is disabled
**File:** `backend/app/security/deps.py`  
**Risk:** Every API endpoint is completely open. Any network-accessible deployment of this build has no access control whatsoever.  
**Fix:** Revert `deps.py` to the original JWT implementation. See `docs/adr/002-auth-removed-for-demo.md` for the full reversal checklist.  
**Effort:** ~2 hours

### 2. Hardcoded secrets in docker-compose.yml
**Risk:** `SECRET_KEY`, `POSTGRES_PASSWORD`, and `GF_SECURITY_ADMIN_PASSWORD` are hardcoded in `docker-compose.yml`. These must never be committed to a repository with these values.  
**Fix:** Move all secrets to a `.env` file (gitignored) or a secrets manager. Use `docker secret` or Kubernetes secrets in production.  
**Effort:** 1 hour

### 3. No HTTPS
**Risk:** The nginx reverse proxy serves HTTP only. Credentials (if auth is re-enabled), API tokens, and user data transit in plaintext.  
**Fix:** Add TLS termination at the nginx layer or the load balancer in front of it.  
**Effort:** 2–4 hours depending on certificate management approach

---

## 🟡 High — address in first sprint

### 4. agg_revenue_daily requires manual refresh after DB volume reset
**Symptom:** After `docker compose down -v`, re-running `generate_synthetic_data.py` loads data into `fact_transactions` but training sees only 1 global segment.  
**Root cause:** Deduplication fingerprints are wiped with the volume, so all records re-insert, but the refresh is only called when `affected_dates` is non-empty. The unconditional fallback refresh (ADR 004) handles most cases but requires the ingestion script to be run again.  
**Fix:** On container startup, call `refresh_agg_revenue_daily(NULL)` in the lifespan function after `create_all_tables()`.  
**Effort:** 30 minutes

### 5. Prophet is in pyproject.toml but cannot be used
**File:** `backend/pyproject.toml`  
`prophet==1.1.6` and `cmdstanpy>=1.2.0` are declared dependencies. They install successfully but Prophet cannot train because CmdStan binary is not installed in the container.  
**Risk:** A developer unfamiliar with ADR 001 may try to re-enable Prophet and spend hours debugging the `stan_backend` error.  
**Fix:** Either remove Prophet from `pyproject.toml` entirely, or add a comment and a `Dockerfile.api.prophet` variant with CmdStan pre-installed.  
**Effort:** 30 minutes to document; 4 hours to implement the Prophet variant

### 6. No input validation on forecast horizon
The `horizon_days` parameter is validated to be between 1 and 365, but the feature matrix builder generates lag features with windows up to 90 days. Requesting `horizon_days=365` with fewer than 365 training rows will produce a forecast of degrading quality without any warning.  
**Fix:** Add a warning response field when `horizon_days > len(training_data) / 2`.  
**Effort:** 2 hours

### 7. Scheduler runs Prophet-included model_names from cached training_runs rows
The APScheduler reads `model_names` from `training_runs` rows created by previous pipeline runs. If any historical run stored `["prophet", "lightgbm"]` in `model_names`, the scheduler will pass that list to `run_training_pipeline` on the next scheduled retrain. The runtime strip (`[m for m in model_names if m != "prophet"]`) handles this, but old rows are confusing.  
**Fix:** Run `UPDATE training_runs SET model_names = '["lightgbm"]' WHERE model_names @> '["prophet"]'::jsonb;` once after deployment.  
**Effort:** 5 minutes

---

## 🟢 Medium — address in first month

### 8. No test coverage for the frontend
The backend has ~2,200 lines of tests across unit and integration suites. The React frontend has no tests at all — no component tests, no API mock tests, no E2E.  
**Fix:** Add Vitest unit tests for key components (ForecastExplorer, FilterContext, useAsync) and Playwright E2E tests for the train → forecast flow.  
**Effort:** 3–5 days

### 9. Forecasts are not invalidated when new data arrives
When new transaction records are ingested after forecasts have been generated, `forecast_results` is not cleared. The chart will show old forecasts generated on the previous data until the user manually clicks "Generate forecast" again.  
**Fix:** Add a flag or timestamp to indicate forecast staleness; show a "New data available — regenerate forecast" banner in the UI.  
**Effort:** 4 hours

### 10. No rate limiting on the AI insight endpoints
Each call to the insight engines makes one or more DeepSeek API calls. A user can hammer the "Trend Summary" button and exhaust API credits quickly.  
**Fix:** Add per-user (or global) rate limiting on `/api/v1/insights/*` endpoints using the existing `InsightCache` table for deduplication.  
**Effort:** 3 hours

### 11. LightGBM model is re-trained on every predict call
The predictor does not persist trained model artifacts to disk. Every call to `predict_segment` re-trains the model from scratch on the full feature matrix. For a 450-day dataset this takes ~1 second, but for larger datasets this will become a bottleneck.  
**Fix:** Serialize trained model artifacts (e.g. with `joblib`) to a file store or the `feature_store` table, and load the cached artifact on predict instead of re-training.  
**Effort:** 1–2 days

### 12. Docker Compose only — no Kubernetes manifests
The platform is designed for a single-machine Docker Compose deployment. There are no Kubernetes manifests, no Helm chart, and no horizontal scaling configuration.  
**Fix:** See `docs/cicd-plan.md` for the recommended production architecture.  
**Effort:** 3–5 days for initial K8s manifests

---

## 🔵 Low — technical debt / nice to have

### 13. Global segment forecast may not reflect category/region filters
When a user selects a specific category and region but the specific model falls back to the global model (ADR 003), the forecast shown is based on global revenue patterns, not the selected segment. There is no UI indicator that a fallback occurred.

### 14. Grafana dashboards are provisioned but not populated
The Grafana provisioning config mounts the dashboard JSON files, but the dashboards themselves show "No data" until Prometheus has scraped enough data points (requires ~5 minutes of uptime with traffic).

### 15. The `audit_log` foreign key on `user_id` is effectively nullable
In demo mode, `user_id=0` is written to `audit_log` for every audited action. `users.user_id=0` does not exist. The column is nullable in the schema so inserts succeed, but any query joining `audit_log` to `users` on `user_id` will drop all demo-mode rows.
