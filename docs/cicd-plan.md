# CI/CD Plan — CPG Platform Production Pipeline

**Audience:** Receiving engineering team  
**Purpose:** One-page design for the production CI/CD pipeline. Intended as a starting point, not a final specification.

---

## Environments

| Environment | Purpose | Deployment trigger | Data |
|---|---|---|---|
| **dev** | Feature development, rapid iteration | Push to any feature branch | Synthetic (generate_synthetic_data.py) |
| **staging** | Integration testing, demo runs, stakeholder review | Merge to `main` | Synthetic, anonymised subset of prod |
| **production** | Live business analytics | Manual promotion from staging (with approval gate) | Real transaction data |

All three environments share the same Docker images — only configuration (env vars, DB URLs, secrets) differs.

---

## Pipeline stages

```
Code push
    │
    ▼
┌─────────────────────────────────────────────┐
│  Stage 1 — CI  (runs on every push/PR)      │
│                                              │
│  1. Lint + type check                        │
│     ruff check backend/                      │
│     mypy backend/app/ --ignore-missing-imports│
│     eslint frontend/src/                     │
│                                              │
│  2. Unit tests                               │
│     pytest app/tests/unit/ -x --tb=short    │
│                                              │
│  3. Integration tests (Docker Compose)       │
│     docker compose -f docker-compose.test.yml│
│     up --abort-on-container-exit             │
│                                              │
│  4. Build images                             │
│     docker build backend/ → cpg-api:sha      │
│     docker build frontend/ → cpg-web:sha     │
│                                              │
│  5. Push to registry (on main branch only)   │
│     ghcr.io/org/cpg-api:sha                  │
│     ghcr.io/org/cpg-web:sha                  │
└─────────────────────────────────────────────┘
    │  (main branch only)
    ▼
┌─────────────────────────────────────────────┐
│  Stage 2 — Deploy to staging                │
│                                              │
│  1. Pull images tagged :sha                  │
│  2. Run DB migrations (Alembic)              │
│     alembic upgrade head                     │
│  3. Deploy via docker compose (staging host) │
│     or kubectl apply -f k8s/staging/         │
│  4. Smoke test                               │
│     curl /api/v1/health → 200                │
│     curl /api/v1/analytics/summary → 200     │
│  5. Notify team (Slack / email)              │
└─────────────────────────────────────────────┘
    │  (manual approval gate)
    ▼
┌─────────────────────────────────────────────┐
│  Stage 3 — Promote to production            │
│                                              │
│  1. Engineer approves in CI dashboard        │
│  2. Re-tag :sha as :latest and :YYYY-MM-DD   │
│  3. Blue/green deploy                        │
│     - Spin up new containers alongside old   │
│     - Run health check on new               │
│     - Switch load balancer                  │
│     - Keep old containers for 10 min        │
│  4. Post-deploy smoke tests                  │
│  5. Rollback trigger available for 30 min   │
└─────────────────────────────────────────────┘
```

---

## GitHub Actions workflow (skeleton)

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main, 'feature/**']
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: cpg
          POSTGRES_PASSWORD: cpg_secret
          POSTGRES_DB: cpg_platform_test
        ports: ['5433:5432']
        options: --health-cmd "pg_isready -U cpg" --health-interval 5s

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: pip

      - name: Install backend deps
        run: pip install -e ".[dev]"
        working-directory: backend

      - name: Lint
        run: ruff check app/
        working-directory: backend

      - name: Unit tests
        run: pytest app/tests/unit/ -v --tb=short
        working-directory: backend
        env:
          DATABASE_URL: postgresql+psycopg2://cpg:cpg_secret@localhost:5433/cpg_platform_test

      - name: Integration tests
        run: pytest app/tests/integration/ -v --tb=short
        working-directory: backend
        env:
          DATABASE_URL: postgresql+psycopg2://cpg:cpg_secret@localhost:5433/cpg_platform_test

  build:
    runs-on: ubuntu-latest
    needs: test
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push API image
        uses: docker/build-push-action@v5
        with:
          context: backend
          file: backend/docker/Dockerfile.api
          push: true
          tags: |
            ghcr.io/${{ github.repository }}/cpg-api:${{ github.sha }}
            ghcr.io/${{ github.repository }}/cpg-api:latest

      - name: Build and push web image
        uses: docker/build-push-action@v5
        with:
          context: frontend
          file: frontend/Dockerfile
          push: true
          tags: |
            ghcr.io/${{ github.repository }}/cpg-web:${{ github.sha }}
            ghcr.io/${{ github.repository }}/cpg-web:latest
```

---

## Promotion strategy

**Dev → Staging:** Automatic on every merge to `main`. No human approval required. Staging should always reflect the latest `main`.

**Staging → Production:** Manual approval from one of: Engineering Lead, Product Owner, or Senior Engineer. Approval is recorded in the CI dashboard with the approver's name and timestamp.

**Rollback:** Keep the previous production image tags (`:prev`) available for 7 days. Rollback procedure: re-deploy `:prev` tag using the same deployment pipeline. Target RTO: < 5 minutes.

---

## Quality gates

The following must pass before promotion to production:

| Gate | Tool | Threshold |
|---|---|---|
| Unit tests | pytest | 100% pass |
| Integration tests | pytest + Docker | 100% pass |
| Code coverage | pytest-cov | ≥ 70% |
| Lint | ruff | 0 errors |
| API health check | curl | HTTP 200 within 60s of deploy |
| Smoke test — analytics | curl /api/v1/analytics/summary | HTTP 200, non-empty response |
| Smoke test — forecast retrieve | curl /api/v1/forecasting/forecasts | HTTP 200 |
| No critical CVEs | trivy image scan | 0 CRITICAL severity |

---

## Secrets management

| Secret | Dev | Staging | Production |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | `.env` file (gitignored) | GitHub Actions secret | AWS Secrets Manager / Vault |
| `SECRET_KEY` (JWT) | Any string | GitHub Actions secret | AWS Secrets Manager / Vault |
| `POSTGRES_PASSWORD` | `docker-compose.yml` | GitHub Actions secret | AWS RDS managed credentials |
| `GF_SECURITY_ADMIN_PASSWORD` | `docker-compose.yml` | GitHub Actions secret | Vault |

Never commit secrets to the repository. Use `git-secrets` or `gitleaks` as a pre-commit hook.

---

## Infrastructure recommendation (production)

For a team of 5–20 analysts with moderate usage:

```
Route 53 / CloudFlare DNS
        │
        ▼
Application Load Balancer (HTTPS termination)
        │
        ├── ECS Fargate — cpg-web (nginx + React, 0.25 vCPU / 512MB, 2 replicas)
        │
        └── ECS Fargate — cpg-api (FastAPI, 1 vCPU / 2GB, 2 replicas)
                │
                ├── RDS PostgreSQL 16 (db.t3.medium, Multi-AZ)
                │
                └── CloudWatch / Prometheus (metrics forwarded from OTel collector)
```

Estimated monthly cost: ~$200–400 USD depending on traffic and RDS instance size.

---

## Database migration strategy

Use **Alembic** for all schema changes. No raw SQL edits to production.

```bash
# Generate migration
alembic revision --autogenerate -m "add index on forecast_results.forecast_date"

# Apply to staging
alembic upgrade head

# Rollback one step
alembic downgrade -1
```

The init SQL files in `db/init/` are for fresh deployments only. All subsequent schema changes go through Alembic migrations in `backend/alembic/versions/`.
