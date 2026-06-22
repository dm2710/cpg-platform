"""
OpenTelemetry instrumentation.

Wires up:
  - Distributed tracing (FastAPI, SQLAlchemy, httpx auto-instrumented)
  - Metrics (request count/latency, custom business metrics)
  - Trace-ID injection into structlog output, so every log line can be
    correlated with the trace that produced it

All telemetry is exported via OTLP/gRPC to the otel-collector service,
which fans it out to Prometheus (metrics) and any configured tracing
backend (Tempo, Jaeger, etc. — not bundled by default, but the
collector config is ready for one).

If OTEL_EXPORTER_OTLP_ENDPOINT is not set, tracing/metrics are
no-ops (the SDK falls back to a no-op provider) so this is always
safe to import, including in tests and local dev without a collector.
"""

from __future__ import annotations

import os

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.core.config import get_settings

settings = get_settings()

_initialized = False


def configure_otel() -> None:
    """Initialize OpenTelemetry SDK. Idempotent — safe to call multiple times."""
    global _initialized
    if _initialized:
        return

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    service_name = os.environ.get("OTEL_SERVICE_NAME", "cpg-api")

    resource = Resource.create({
        "service.name": service_name,
        "service.version": settings.version,
        "deployment.environment": settings.environment,
    })

    # ── Tracing ────────────────────────────────────────────
    tracer_provider = TracerProvider(resource=resource)
    if endpoint:
        span_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)

    # ── Metrics ────────────────────────────────────────────
    if endpoint:
        metric_exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True)
        metric_reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=15000)
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    else:
        meter_provider = MeterProvider(resource=resource)
    metrics.set_meter_provider(meter_provider)

    _initialized = True


def instrument_app(app) -> None:
    """Auto-instrument FastAPI, SQLAlchemy, and outbound httpx calls."""
    FastAPIInstrumentor.instrument_app(app)
    SQLAlchemyInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()


def get_tracer(name: str):
    return trace.get_tracer(name)


def get_meter(name: str):
    return metrics.get_meter(name)


def trace_id_processor(logger, method_name, event_dict):
    """
    structlog processor — injects the active OTel trace_id/span_id into
    every log line, so logs can be correlated with traces in Grafana.
    No-op (adds nothing) if there's no active span.
    """
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict
