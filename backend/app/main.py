"""FastAPI application factory -- Phase 1 + 2 + 3 + 4 + 5 (production hardening)."""

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from app.core.config import get_settings
from app.core.database import check_connection, create_all_tables
from app.core.logging import configure_logging, get_logger
from app.core.telemetry import configure_otel, instrument_app
from app.scheduler.retraining import start_scheduler, stop_scheduler
from app.security.audit import AuditLogMiddleware

# Import all ORM models so Base.metadata is fully populated before
# create_all_tables() is called. Without this, SQLAlchemy's create_all
# has no tables to create and the DB schema is left incomplete.
import app.models  # noqa: F401  registers all models with Base.metadata

log      = get_logger(__name__)
settings = get_settings()


def _wait_for_db(retries: int = 20, delay: float = 3.0) -> None:
    for attempt in range(1, retries + 1):
        if check_connection():
            log.info("db.connected", attempt=attempt)
            return
        log.warning("db.not_ready_for_sql", attempt=attempt, of=retries, retry_in=delay)
        time.sleep(delay)
    raise RuntimeError(f"PostgreSQL accepted TCP but could not execute SQL after {retries} attempts.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    configure_otel()
    log.info("app.starting", version=settings.version, env=settings.environment)
    _wait_for_db(retries=20, delay=3.0)
    if not settings.is_production:
        create_all_tables()
    start_scheduler()
    log.info("app.ready")
    yield
    stop_scheduler()
    log.info("app.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.project_name,
        version=settings.version,
        description="CPG Predictive Intelligence Platform -- Phase 1-5 (production)",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins if settings.is_production else ["*"],
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(AuditLogMiddleware)

    # OpenTelemetry auto-instrumentation (FastAPI, SQLAlchemy, httpx)
    instrument_app(app)

    # Prometheus /metrics endpoint -- request count and latency
    # histograms, broken down by handler/method/status. This is the
    # library's default metric bundle (stable across versions); see
    # https://github.com/trallnag/prometheus-fastapi-instrumentator
    # if you want to add the in-progress-requests gauge or other
    # optional metrics -- left out here since this sandbox can't
    # install the package to verify exact optional-parameter names
    # against the pinned version.
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

    from app.api.v1.endpoints import (
        analytics, auth, conversation, data_quality, forecasting,
        health, ingestion, insights, reference, reports, retraining,
    )

    prefix = settings.api_v1_prefix
    app.include_router(health.router,       prefix=prefix,                   tags=["Health"])
    app.include_router(auth.router,         prefix=f"{prefix}/auth",         tags=["Auth"])
    app.include_router(ingestion.router,    prefix=f"{prefix}/ingestion",    tags=["Ingestion"])
    app.include_router(analytics.router,    prefix=f"{prefix}/analytics",    tags=["Analytics"])
    app.include_router(reference.router,    prefix=f"{prefix}/reference",    tags=["Reference"])
    app.include_router(data_quality.router, prefix=f"{prefix}/dq",           tags=["Data Quality"])
    app.include_router(forecasting.router,  prefix=f"{prefix}/forecasting",  tags=["Forecasting"])
    app.include_router(insights.router,     prefix=f"{prefix}/insights",     tags=["AI Insights"])
    app.include_router(conversation.router, prefix=f"{prefix}/conversation", tags=["Conversational Analytics"])
    app.include_router(reports.router,      prefix=f"{prefix}/reports",      tags=["Reports"])
    app.include_router(retraining.router,   prefix=f"{prefix}/retraining",   tags=["Automated Retraining"])

    @app.exception_handler(ValueError)
    async def value_error_handler(request, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def general_error_handler(request, exc):
        log.error("unhandled_exception", error=str(exc), path=str(request.url))
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
