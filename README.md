# CPG Predictive Intelligence Platform

Full-stack revenue analytics platform: ingestion, forecasting, AI
insights, and a business dashboard — all four phases in one repo.

```
cpg-platform-full/
├── docker-compose.yml      ← starts everything: postgres + api + web
├── .env.example
├── backend/                ← FastAPI + PostgreSQL (Phases 1–3 + Phase 4 API additions)
│   ├── app/
│   ├── db/
│   ├── docker/
│   └── pyproject.toml
└── frontend/                ← React + Vite dashboard (Phase 4 UI)
    ├── src/
    ├── Dockerfile
    ├── nginx.conf
    └── package.json
```

## Quick start

```bash
docker compose up --build
```

That single command builds and starts three containers:

| Service | URL | What it is |
|---|---|---|
| `web` | http://localhost:5173 | The dashboard (nginx serving the Vite build, proxying `/api` to `api`) |
| `api` | http://localhost:8000 | FastAPI backend — also browsable at `/docs` |
| `postgres` | `localhost:5433` | Database (exposed for local tools like psql/DBeaver) |

Set your DeepSeek key before starting — edit `DEEPSEEK_API_KEY` in
`docker-compose.yml` under the `api` service. The AI Insights and Ask
AI tabs in the dashboard won't work without it.

```bash
docker compose down -v   # full reset, including the database volume
```

## Loading sample data

The dashboard starts empty — there's no data until you load some.
A generator script seeds ~15 months of realistic synthetic CPG
transaction history (5 categories x 5 regions) through the real
ingestion API, so every tab has something genuine to show: revenue
trends, seasonality, a deliberate Dairy demand decline for testing
Root Cause Analysis, and region-to-region variation for Regional
Analysis.

```bash
# With the stack already running (docker compose up)
cd backend
pip install httpx --break-system-packages   # the script's only dependency
python scripts/generate_synthetic_data.py

# Also train forecasting models and generate forecasts in one go:
python scripts/generate_synthetic_data.py --train --forecast
```

Takes well under a minute for the data load; training adds a few
more minutes depending on your machine. Run it again any time to
load fresh history (duplicate transactions are automatically
deduped by the pipeline, so re-running is safe).

Options:
```bash
python scripts/generate_synthetic_data.py --help
python scripts/generate_synthetic_data.py --days 730        # ~2 years instead of ~15 months
python scripts/generate_synthetic_data.py --api-url http://localhost:8000
```

What to look at afterward:
- **Revenue overview** — trend chart should show a clear ~18% growth
  arc across the period plus weekly seasonality
- **Regional analysis** — North America strongest, Middle East weakest
- **Category analysis** — Snacks and Beverages show summer/holiday peaks
- **Forecast explorer** — needs `--train --forecast` to have run first
- **AI insights / Ask AI** — try "Why is dairy forecasted to decline?"
  or open Root Cause Analysis with that same question; the generator
  baked in a real ~22% Dairy demand dip over the last 21 days

## Running tests

```bash
docker compose run --rm test
```

## Local development (without Docker)

**Backend:**
```bash
cd backend
pip install -e ".[dev]" --break-system-packages
export DATABASE_URL=postgresql+psycopg2://cpg:cpg_secret@localhost:5433/cpg_platform
uvicorn app.main:app --reload
```

**Frontend** (in a second terminal, with the backend already running
on port 8000):
```bash
cd frontend
npm install
npm run dev
```
Opens at http://localhost:5173 with hot reload; Vite proxies `/api`
straight to `localhost:8000`.

## What's inside

- **Phase 1 — Data foundation**: six-stage ingestion pipeline (schema
  drift resolution, validation, dedup, FX/unit normalization,
  late-arrival handling, persistence), SCD2 SKU catalog, analytics API.
- **Phase 2 — Forecasting**: Prophet + LightGBM models, feature store,
  walk-forward CV, training/prediction pipelines, accuracy tracking.
- **Phase 3 — AI insights**: five DeepSeek-powered analysis engines
  (trend summarization, root cause, forecast explanation, revenue
  drivers, executive summary), all grounded in live PostgreSQL data
  with a confidence score and a SHA-256 TTL cache.
- **Phase 4 — Business interface**: the React dashboard in
  `frontend/`, plus two backend additions — session-based
  conversational analytics (`/api/v1/conversation`) and PDF/CSV report
  export (`/api/v1/reports`).

See `frontend/README.md` for dashboard-specific details and
`backend/` for the API source.
