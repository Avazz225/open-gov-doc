"""Distributed tracing (concept 10.1, P94-S1, ADR 0250).

`setup_tracing(app, settings)` is the one call every service makes right
after creating its FastAPI app. Without `DMS_OTEL_EXPORTER_ENDPOINT` it does
nothing at all (no provider, no instrumentation, no cost). With it:

- a TracerProvider exporting OTLP/HTTP in batches to the collector;
- incoming requests (FastAPI), outgoing calls (httpx) and database statements
  (SQLAlchemy, also async engines) become spans; the W3C `traceparent` header
  carries the trace from service to service, NATS messages carry it too
  (`dms_eventbus_client`);
- a parent-based ratio sampler whose ratio monitoring-service controls (the
  "tracing sampling ratio" next to the sensor configuration) - a request
  either is traced across every hop or not at all.
"""

import logging
import threading

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import (
    Decision,
    ParentBased,
    Sampler,
    SamplingResult,
    TraceIdRatioBased,
)
from opentelemetry.trace import SpanKind

from dms_common.settings import BaseServiceSettings

logger = logging.getLogger(__name__)

# Paths that would only add noise: health checks and the scrape endpoint are
# polled every few seconds by the stack itself.
EXCLUDED_URLS = "healthz,metrics"


# Kinds a trace may start with: an incoming request, a consumed event, an
# event published by a background job. A root span of any other kind - a
# poll loop's database query, its HTTP call to monitoring-service's sensor
# configuration - would flood the store with one-span traces nobody asked for.
ENTRY_KINDS = (SpanKind.SERVER, SpanKind.CONSUMER, SpanKind.PRODUCER)


class AdjustableRatioSampler(Sampler):
    """A trace-id ratio sampler whose ratio can change at runtime (from
    monitoring-service's configuration). Used as the root sampler under
    `ParentBased`, so only a trace's first service decides; every later hop
    follows the `traceparent` flag. Roots that aren't entry points
    (`ENTRY_KINDS`) are never sampled."""

    def __init__(self, ratio: float) -> None:
        self._lock = threading.Lock()
        self._inner = TraceIdRatioBased(_clamp(ratio))

    @property
    def ratio(self) -> float:
        return self._inner.rate

    def set_ratio(self, ratio: float) -> None:
        ratio = _clamp(ratio)
        with self._lock:
            if ratio != self._inner.rate:
                self._inner = TraceIdRatioBased(ratio)
                logger.info("tracing_sampling_ratio_changed", extra={"ratio": ratio})

    def should_sample(
        self,
        parent_context,
        trace_id,
        name,
        kind=None,
        attributes=None,
        links=None,
        trace_state=None,
    ) -> SamplingResult:
        if kind not in ENTRY_KINDS:
            return SamplingResult(Decision.DROP)
        return self._inner.should_sample(
            parent_context, trace_id, name, kind, attributes, links, trace_state
        )

    def get_description(self) -> str:
        return f"AdjustableRatioSampler{{{self._inner.rate}}}"


def _clamp(ratio: float) -> float:
    try:
        value = float(ratio)
    except (TypeError, ValueError):
        return 1.0
    return min(1.0, max(0.0, value))


_sampler: AdjustableRatioSampler | None = None


def set_sampling_ratio(ratio: float) -> None:
    """Changes the ratio of new traces started in this process (no-op while
    tracing is off). Called by `dms_metrics_client.SensorConfigClient` with
    the value it polls from monitoring-service."""
    if _sampler is not None:
        _sampler.set_ratio(ratio)


def current_sampling_ratio() -> float | None:
    return _sampler.ratio if _sampler is not None else None


def configure_tracing(
    settings: BaseServiceSettings,
    exporter: SpanExporter,
    *,
    batch: bool = False,
    sampling_ratio: float | None = None,
) -> trace.Tracer:
    """Sets the global TracerProvider and returns a tracer. The exporter is
    passed in (OTLP in the stack, in-memory in tests)."""
    global _sampler
    _sampler = AdjustableRatioSampler(
        settings.otel_sampling_ratio if sampling_ratio is None else sampling_ratio
    )
    provider = TracerProvider(
        resource=Resource.create(
            {
                SERVICE_NAME: settings.service_name,
                "deployment.environment": settings.environment,
                "dms.installation_id": settings.installation_id,
            }
        ),
        sampler=ParentBased(root=_sampler),
    )
    processor = BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    return trace.get_tracer(settings.service_name)


def setup_tracing(app, settings: BaseServiceSettings) -> bool:
    """Wires tracing into a service; returns whether it is on. Must run at
    import time, right after `app = FastAPI(...)` - FastAPI accepts middleware
    only before startup, and httpx clients and database engines created later
    (in `lifespan`) are instrumented because the instrumentors patch their
    constructors."""
    endpoint = settings.otel_exporter_endpoint
    if not endpoint:
        return False
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    configure_tracing(
        settings,
        OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"),
        batch=True,
    )
    # The per-chunk ASGI "http send/receive" spans add nothing to a hop
    # breakdown but triple the span count.
    FastAPIInstrumentor.instrument_app(
        app, excluded_urls=EXCLUDED_URLS, exclude_spans=["receive", "send"]
    )
    if not HTTPXClientInstrumentor().is_instrumented_by_opentelemetry:
        HTTPXClientInstrumentor().instrument()
    _instrument_sqlalchemy()
    _register_ratio_listener()
    logger.info(
        "tracing_enabled",
        extra={"endpoint": endpoint, "sampling_ratio": settings.otel_sampling_ratio},
    )
    return True


def setup_observability(app, settings: BaseServiceSettings) -> bool:
    """The one call every service makes right after `app = FastAPI(...)`
    (P94-S1/S2): the structured access log (always) and tracing (when an
    exporter endpoint is set). Returns whether tracing is on. The access-log
    middleware is added first, so the tracing middleware wraps it and every
    access line carries its request's trace id."""
    from dms_common.access_log import AccessLogMiddleware

    app.add_middleware(AccessLogMiddleware)
    _register_components_endpoint(app, settings)
    return setup_tracing(app, settings)


# Who may read `/component-report` (P100-S1, ADR 0271): only monitoring-service,
# which collects the inventory. A version list helps an attacker, and the
# gateway overwrites `X-DMS-Principal` with the real user, so a request from
# outside can't pass as this principal.
COMPONENTS_READERS = frozenset({"monitoring-service"})


def _register_components_endpoint(app, settings: BaseServiceSettings) -> None:
    from fastapi import Header, HTTPException

    from dms_common.components import runtime_report

    async def components(x_dms_principal: str = Header(default="")) -> dict:
        if x_dms_principal not in COMPONENTS_READERS:
            raise HTTPException(status_code=403, detail="Nur für den Komponenten-Bestand")
        return runtime_report(settings.service_name)

    # Not `/components`: that path is monitoring-service's inventory API.
    app.add_api_route("/component-report", components, methods=["GET"], include_in_schema=False)


def _instrument_sqlalchemy() -> None:
    """Database statements as spans - only where the service has a database
    (gateway-, config-service and the two connectors have none)."""
    try:
        import sqlalchemy  # noqa: F401
    except ImportError:
        return
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    if not SQLAlchemyInstrumentor().is_instrumented_by_opentelemetry:
        # `enable_commenter` stays off: statement text must not change.
        SQLAlchemyInstrumentor().instrument()


def _register_ratio_listener() -> None:
    """monitoring-service's sampling ratio arrives with the sensor
    configuration every service already polls - optional, a service without
    the metrics client keeps its configured ratio."""
    try:
        from dms_metrics_client.config_client import register_tracing_ratio_listener
    except ImportError:
        return
    register_tracing_ratio_listener(set_sampling_ratio)


__all__ = [
    "AdjustableRatioSampler",
    "configure_tracing",
    "current_sampling_ratio",
    "set_sampling_ratio",
    "setup_observability",
    "setup_tracing",
]
