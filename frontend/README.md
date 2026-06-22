# CPG Platform — Revenue Intelligence Dashboard

Phase 4 business interface. React + Vite frontend calling the FastAPI
backend in `../backend`.

This package is part of the combined `cpg-platform-full` repo — see
the root `README.md` for the one-command Docker startup. The
instructions below are for running the frontend on its own, e.g.
during active UI development with hot reload.

## Local development

With the backend already running (either via `docker compose up` from
the repo root, or `uvicorn app.main:app --reload` from `../backend`):

```bash
npm install
npm run dev
```

Opens at `http://localhost:5173`. Vite proxies `/api/*` to
`http://localhost:8000` (see `vite.config.js`). Override with:

```bash
VITE_API_PROXY_TARGET=http://localhost:8000 npm run dev
```

## Production build

```bash
npm run build
```

Outputs static files to `dist/`. The repo-root `docker-compose.yml`
builds this automatically via the multi-stage `Dockerfile` (Vite
build → nginx), which also reverse-proxies `/api/` to the `api`
container by Docker service name — no environment variable needed in
that path, since `nginx.conf` points directly at `http://api:8000`.

## Pages

| Route | Page | Backend calls |
|---|---|---|
| `/` | Revenue overview | `/analytics/revenue`, `/analytics/breakdown`, `/analytics/summary` |
| `/categories` | Category analysis | `/analytics/breakdown`, `/insights/drivers` |
| `/regions` | Regional analysis | `/analytics/breakdown`, `/insights/trend` |
| `/forecast` | Forecast explorer | `/forecasting/train`, `/forecasting/predict`, `/forecasting/forecasts`, `/insights/forecast/explain` |
| `/insights` | AI insights | all five `/insights/*` engines |
| `/ask` | Ask AI | `/conversation/*` |

The sidebar's category/region/lookback-window filters are shared
global state (`src/context/FilterContext.jsx`) and apply across all
pages except Ask AI, where the active segment is attached per-message.

## Design

Visual identity: ledger-paper neutrals, deep ink sidebar, single
harvest-amber accent. `Fraunces` for headlines and KPI numbers,
`Inter` for UI text, `JetBrains Mono` for tabular figures. Tokens live
in `src/styles/tokens.css`.
