# CPG Platform — Revenue Intelligence Dashboard

Full-stack demand forecasting and analytics platform for Consumer Packaged Goods teams. Covers data ingestion, revenue analytics, ML-based forecasting, AI-powered insights, and conversational analytics — all running locally via Docker Compose.

---

## Quick start

### Prerequisites

- Docker Desktop ≥ 4.x
- Python ≥ 3.11 (for the data-loading script only)
- `httpx` Python package: `pip install httpx`

### 1. Clone and configure

```bash
git clone <repo-url>
cd cpg-platform
```

Open `docker-compose.yml` and set your DeepSeek API key (required for AI Insights and Ask AI tabs):

```yaml
DEEPSEEK_API_KEY: "sk-your-key-here"   # get one at platform.deepseek.com
```

### 2. Build and start

```bash
docker compose build --no-cache
docker compose up -d
```

Wait ~60 seconds for all services to become healthy. Check status:

```bash
docker compose ps
```

All services should show `healthy` or `Up`. The API health endpoint:

```bash
curl http://localhost:8000/api/v1/health
# → {"status": "ok", ...}
```

### 3. Load synthetic data

```bash
python backend/scripts/generate_synthetic_data.py --train --forecast
```

This generates 11,250 transaction records (5 categories × 5 regions × 450 days), pushes them through the ingestion pipeline, trains a LightGBM model per segment, and generates 30-day forecasts.

Expected output:
```
Generated 11,250 records (5 categories × 5 regions × 450 days)
  Batch 1/12: 938 accepted, 0 rejected, 0 duplicates
  ...
Training complete: 26 segments trained, avg MAPE 10.3%
Forecast complete: 26 segments forecasted
```

### 4. Open the dashboard

```
http://localhost:5173
```

---

## Services

| Service | URL | Description |
|---|---|---|
| Dashboard | http://localhost:5173 | React frontend |
| API | http://localhost:8000 | FastAPI backend |
| API docs | http://localhost:8000/docs | Swagger UI |
| Grafana | http://localhost:3000 | Observability dashboards (admin / admin) |
| Prometheus | http://localhost:9090 | Metrics |

---

## Project structure

```
cpg-platform/
├── docker-compose.yml
├── backend/
│   ├── app/
│   │   ├── api/v1/endpoints/     # FastAPI routers
│   │   ├── forecasting/          # ML pipeline (features, training, prediction)
│   │   ├── insights/             # AI insight engines + LLM client
│   │   ├── pipeline/             # Ingestion orchestrator
│   │   ├── security/             # Auth + RBAC (no-op in demo mode)
│   │   └── tests/                # Unit + integration tests
│   ├── db/
│   │   ├── init/                 # SQL schema files (run on fresh DB)
│   │   └── schema*.sql           # Individual schema files
│   ├── scripts/
│   │   ├── generate_synthetic_data.py
│   │   └── seed_admin.py
│   ├── docker/
│   │   └── Dockerfile.api
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── pages/                # Dashboard pages
│   │   ├── components/           # Charts, layout, common UI
│   │   ├── context/              # FilterContext
│   │   ├── hooks/                # useAsync
│   │   └── api/client.js         # Axios wrapper
│   ├── Dockerfile
│   └── nginx.conf
├── observability/
│   ├── prometheus/prometheus.yml
│   └── grafana/
└── docs/
    └── adr/                      # Architecture Decision Records
```

---

## Running tests

Tests require the postgres container to be running:

```bash
docker compose up -d postgres
docker compose run --rm test
```

Or run directly with pytest (requires local postgres on port 5433):

```bash
cd backend
pip install -e ".[dev]"
pytest app/tests/ -v --tb=short
```

---

## Re-seeding after a database reset

If you run `docker compose down -v` (which deletes volumes), re-run:

```bash
docker compose up -d
sleep 60
python backend/scripts/generate_synthetic_data.py --train --forecast
```

To manually refresh the aggregate table without re-loading data:

```bash
docker exec cpg_postgres psql -U cpg -d cpg_platform -c \
  "SELECT refresh_agg_revenue_daily(NULL);"
```

---

## Known limitations (demo build)

- **No authentication** — all endpoints are open. See `docs/adr/002-auth-removed-for-demo.md`
- **Prophet removed** — LightGBM only. See `docs/adr/001-lightgbm-only-no-prophet.md`
- **No CI pipeline** — Docker Compose only. See `docs/cicd-plan.md` for the production pipeline design
- **DeepSeek required** for AI features — without a valid key, insight endpoints return a friendly "not configured" message instead of erroring

---

## Architecture decisions

See [`docs/adr/`](docs/adr/) for all recorded decisions:

- [ADR 001](docs/adr/001-lightgbm-only-no-prophet.md) — LightGBM only; Prophet removed
- [ADR 002](docs/adr/002-auth-removed-for-demo.md) — Auth removed for demo environment
- [ADR 003](docs/adr/003-forecast-fallback-chain.md) — Forecast fallback chain for sparse segments
- [ADR 004](docs/adr/004-aggregate-refresh-strategy.md) — Always refresh agg_revenue_daily after ingestion
- [ADR 005](docs/adr/005-batch-predict-endpoint.md) — Use /predict/batch for the Generate forecast button
