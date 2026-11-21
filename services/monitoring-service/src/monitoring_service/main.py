import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from monitoring_service import alert_store, alerting, repository, scraper
from monitoring_service.clients import AuthServiceClient, PermissionServiceClient, RegistryClient
from monitoring_service.models import Base
from monitoring_service.schemas import (
    AlertPreviewOut,
    AlertRuleIn,
    AlertRuleOut,
    AlertSeriesOut,
    DowntimeWindowIn,
    DowntimeWindowOut,
    GlobalSensorConfigIn,
    SensorConfigOut,
    SensorOut,
    SensorOverrideIn,
)
from monitoring_service.settings import Settings
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, generate_latest
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


async def publish_event(
    event_type: str, subject: str | None, payload: dict, *, actor: str | None = None
) -> None:
    event = Event(
        event_type=event_type,
        service_name=settings.service_name,
        subject=subject,
        payload=payload,
        actor=actor,
    )
    await app.state.event_bus.publish(event_type, event.to_bytes())


async def _is_active_superuser(x_dms_principal: str) -> bool:
    """Fails safe on an unreachable auth-service (same fix as
    `permission-service`'s own `_is_active_superuser` this session) -
    previously unguarded, an auth-service outage would 500 every caller
    instead of just losing the (optional) superuser bypass."""
    try:
        active, superuser_principal_id = await app.state.auth_client.get_active_superuser()
    except Exception:
        logger.warning(
            "superuser_status_check_failed - auth-service nicht erreichbar?", exc_info=True
        )
        return False
    return active and bool(x_dms_principal) and superuser_principal_id == x_dms_principal


async def _require_monitoring_permission(x_dms_principal: str) -> None:
    """Enforces the new domain-separated admin role
    `domain-admin-monitoring` (`admin.monitoring`, P11-S1) - the activated
    superuser (4.6) is the only exception, same gate pattern as
    license-service/plugin-orchestration-service. Concept 10.1: disabling
    security-relevant sensors is itself a security-relevant,
    audited operation."""
    if await _is_active_superuser(x_dms_principal):
        return
    allowed = bool(x_dms_principal) and await app.state.permission_client.has_permission(
        x_dms_principal, settings.monitoring_permission
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fehlende Domain-Admin-Rolle 'Monitoring-/Sensor-Konfiguration'",
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()

    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS monitoring"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    async with app.state.session_factory() as session:
        await repository.ensure_global_default_seeded(session)
        await repository.migrate_legacy_cost_keys(session)
        await session.commit()

    app.state.auth_client = AuthServiceClient(settings.auth_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)
    app.state.registry_client = RegistryClient(settings.registry_service_base_url)
    app.state.scrape_client = httpx.AsyncClient()

    # Always-active, non-configurable operational counter (P11-S1
    # decision: a self-reference - monitoring-service asking itself
    # via HTTP whether this very sensor is active - would be unnecessary
    # complexity without real benefit). Own CollectorRegistry, separate
    # from the scraped domain service metrics.
    app.state.own_registry = CollectorRegistry()
    app.state.scrape_failures_counter = Counter(
        "monitoring_scrape_failures_total",
        "Anzahl fehlgeschlagener Scrape-Versuche gegen Fach-Service-/metrics-Endpunkte",
        registry=app.state.own_registry,
    )

    event_bus = NatsEventBusClient(settings.nats_url, stream="monitoring")
    await event_bus.connect()
    app.state.event_bus = event_bus

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    # Alerting (P88-S5, ADR 0245): one evaluator per tick across replicas.
    app.state.family_types = {}
    alert_task = asyncio.create_task(_alert_loop())

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis)

    yield

    alert_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await alert_task
    if registration:
        await registration.stop()
    await event_bus.close()
    await app.state.scrape_client.aclose()
    await app.state.registry_client.close()
    await app.state.auth_client.close()
    await app.state.permission_client.close()
    await engine.dispose()


async def _scrape_families() -> dict:
    """Scrapes and merges every instance, exactly like `GET /metrics`."""
    instances = await app.state.registry_client.list_active_instances()
    bodies = await scraper.scrape_targets(
        app.state.scrape_client,
        instances,
        timeout_seconds=settings.scrape_timeout_seconds,
        on_failure=app.state.scrape_failures_counter.inc,
    )
    service_types = {i.instance_id: i.service_type for i in instances}
    return {family.name: family for family in scraper.merge_metric_families(bodies, service_types)}


async def run_alert_tick(families: dict | None = None) -> alert_store.TickResult | None:
    """One evaluation (Decision 4): only the replica that gets the advisory
    lock evaluates; events are published after the commit. `families` can
    be passed in (tests); otherwise every instance is scraped like `GET
    /metrics` does."""
    now = datetime.now(UTC)
    async with app.state.session_factory() as session:
        got_lock = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": alert_store.ALERT_LOCK_KEY}
        )
        if not got_lock:
            return None
        if families is None:
            families = await _scrape_families()
        app.state.family_types = {name: family.type for name, family in families.items()}
        result = await alert_store.tick(session, families, now=now, timezone=settings.timezone)
        await session.commit()
    for event_type, subject, payload in result.events:
        await publish_event(event_type, subject, payload, actor="system:monitoring-alerting")
    return result


async def _alert_loop() -> None:
    while True:
        await asyncio.sleep(settings.alert_evaluation_interval_seconds)
        try:
            await run_alert_tick()
        except Exception:
            logger.exception("alert_tick_failed")


app = FastAPI(title=settings.service_name, lifespan=lifespan)


class _StaticMetricsCollector:
    """Passes an already fully merged list of `Metric` objects
    unchanged to `generate_latest()` - `scraper.merge_metric_families`
    delivers this list freshly per request (10.1: live scrape instead
    of a cached snapshot)."""

    def __init__(self, metrics: list) -> None:
        self._metrics = metrics

    def collect(self):
        yield from self._metrics


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/metrics")
async def get_metrics() -> Response:
    """Scrape proxy (10.1, P11-S1): the only target that Prometheus
    actually queries. Fetches the /metrics endpoints of all currently
    active, healthy instances live and in parallel (address list comes
    from `registry-service`) and merges them into a single response."""
    instances = await app.state.registry_client.list_active_instances()
    bodies = await scraper.scrape_targets(
        app.state.scrape_client,
        instances,
        timeout_seconds=settings.scrape_timeout_seconds,
        on_failure=app.state.scrape_failures_counter.inc,
    )
    service_types = {instance.instance_id: instance.service_type for instance in instances}
    merged = scraper.merge_metric_families(bodies, service_types)

    combined_registry = CollectorRegistry()
    combined_registry.register(_StaticMetricsCollector(merged))
    level_registry = CollectorRegistry()
    level_registry.register(_StaticMetricsCollector([await _alert_level_family()]))
    body = (
        generate_latest(combined_registry)
        + generate_latest(app.state.own_registry)
        + generate_latest(level_registry)
    )
    return Response(content=body, media_type=CONTENT_TYPE_LATEST)


async def _alert_level_family() -> GaugeMetricFamily:
    """`monitoring_alert_level{rule,sensor}` (P88-S7, ADR 0252): each enabled
    rule's evaluated level (-1 unknown, 0 ok, 1 warning, 2 error), so Grafana
    shows the same alarm states as the admin page without a second engine.
    Read from the database on every scrape rather than cached in memory: only
    the replica holding the advisory lock evaluates (ADR 0245 Decision 4), so
    another replica's memory would lag. Always on and outside the sensor
    catalog, like `monitoring_scrape_failures_total`."""
    family = GaugeMetricFamily(
        "monitoring_alert_level",
        "Ausgewerteter Alarmzustand je Regel (-1 unbekannt, 0 ok, 1 Warnung, 2 Fehler)",
        labels=["rule", "sensor"],
    )
    async with app.state.session_factory() as session:
        states = await alert_store.states_by_rule(session)
        rules = await alert_store.list_rules(session)
    for rule in rules:
        if not rule.enabled:
            continue
        state = states.get(rule.id)
        family.add_metric(
            [rule.name, rule.sensor], alerting.level_value(state.level if state else None)
        )
    return family


@app.get("/sensors", response_model=list[SensorOut])
async def list_sensors(session: AsyncSession = Depends(get_session)) -> list[SensorOut]:
    instances = await app.state.registry_client.list_active_instances()
    return await repository.list_sensors(session, instances)


@app.get("/sensor-config", response_model=SensorConfigOut)
async def get_sensor_config(session: AsyncSession = Depends(get_session)) -> SensorConfigOut:
    """Ungated, polled by `dms_metrics_client.SensorConfigClient` -
    pure read access to information already publicly visible via
    `GET /sensors`, no role check needed."""
    return await repository.get_sensor_config(session)


@app.put("/sensor-config/global", response_model=SensorConfigOut)
async def set_global_sensor_config(
    payload: GlobalSensorConfigIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> SensorConfigOut:
    await _require_monitoring_permission(x_dms_principal)
    result = await repository.set_global_default(session, payload.enabled)
    await session.commit()
    await publish_event(
        "monitoring.sensor.config_changed",
        subject="__global__",
        payload={"enabled": payload.enabled, "actor": x_dms_principal},
    )
    return result


@app.put("/sensor-config/{sensor_name}", response_model=SensorConfigOut)
async def set_sensor_override(
    sensor_name: str,
    payload: SensorOverrideIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> SensorConfigOut:
    await _require_monitoring_permission(x_dms_principal)
    try:
        keys = repository.expand_override_key(sensor_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for key in keys:
        result = await repository.set_sensor_override(session, key, payload.enabled)
    await session.commit()
    for key in keys:
        await publish_event(
            "monitoring.sensor.config_changed",
            subject=key,
            payload={"enabled": payload.enabled, "actor": x_dms_principal},
        )
    return result


# --- Alerting (P88-S5, ADR 0245) -------------------------------------------------


def _rule_out(rule, state) -> AlertRuleOut:
    fields = {key: getattr(rule, key) for key in AlertRuleIn.model_fields}
    out = AlertRuleOut(**fields, id=rule.id, updated_at=rule.updated_at, updated_by=rule.updated_by)
    if state is not None:
        out.level = state.level
        out.value = state.value
        out.since = state.since
        out.evaluated_at = state.evaluated_at
        out.notified_level = state.notified_level
        out.suppressed = state.suppressed
    return out


def _window_out(window) -> DowntimeWindowOut:
    fields = {key: getattr(window, key) for key in DowntimeWindowIn.model_fields}
    return DowntimeWindowOut(
        **fields,
        id=window.id,
        updated_at=window.updated_at,
        updated_by=window.updated_by,
        active=alerting.window_active(
            enabled=window.enabled,
            starts_at=window.starts_at,
            ends_at=window.ends_at,
            weekdays=window.weekdays,
            daily_start=window.daily_start,
            daily_end=window.daily_end,
            now=datetime.now(UTC),
            timezone=settings.timezone,
        ),
    )


@app.get("/alert-rules", response_model=list[AlertRuleOut])
async def list_alert_rules(session: AsyncSession = Depends(get_session)) -> list[AlertRuleOut]:
    """Every rule with its evaluated state. Ungated, like `GET /sensors`."""
    states = await alert_store.states_by_rule(session)
    return [_rule_out(rule, states.get(rule.id)) for rule in await alert_store.list_rules(session)]


@app.post("/alert-rules/preview", response_model=AlertPreviewOut)
async def preview_alert_rule(payload: AlertRuleIn) -> AlertPreviewOut:
    """Evaluates a draft rule against a fresh scrape (P88-S6), persisting
    nothing - the rule editor's current value and the series the filter
    matches. Ungated like `GET /metrics`, which serves the same values. A
    rate has no value on a single scrape; the editor says so."""
    families = await _scrape_families()
    series = alerting.matching_series(
        families, payload.sensor, payload.aggregation, payload.label_filter
    )
    reason = None
    value = None
    level = "unknown"
    if not series:
        reason = "no_samples"
    elif payload.aggregation == alerting.RATE_AGGREGATION:
        reason = "rate_needs_history"
    else:
        value = alerting.measure(
            [v for _, v in series],
            payload.aggregation,
            last_total=None,
            last_at=None,
            now=datetime.now(UTC),
        ).value
        thresholds = alerting.Thresholds(
            payload.min_error, payload.min_warning, payload.max_warning, payload.max_error
        )
        # Level as if the repeat counts were already met - "would alarm".
        level = alerting.next_level(
            value,
            thresholds,
            warning_repeat=1,
            error_repeat=1,
            warning_streak=0,
            error_streak=0,
        ).level
    return AlertPreviewOut(
        value=value,
        level=level,
        series=[AlertSeriesOut(labels=labels, value=v) for labels, v in series[:50]],
        reason=reason,
    )


@app.get("/alert-rules/prometheus")
async def export_alert_rules(session: AsyncSession = Depends(get_session)) -> Response:
    """The rules as a Prometheus rule file, for installations that run
    Alertmanager anyway (Decision 1) - an export, not a second engine."""
    body = alert_store.prometheus_rules(
        await alert_store.list_rules(session),
        app.state.family_types,
        settings.alert_evaluation_interval_seconds,
    )
    return Response(content=body, media_type="application/yaml")


async def _save_rule(payload: AlertRuleIn, principal: str, session, rule_id: str | None):
    await _require_monitoring_permission(principal)
    try:
        rule = await alert_store.save_rule(
            session, payload.model_dump(), actor=principal, now=datetime.now(UTC), rule_id=rule_id
        )
    except alert_store.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "monitoring.alert_rule.created" if rule_id is None else "monitoring.alert_rule.updated",
        rule.id,
        {"name": rule.name, "sensor": rule.sensor, "enabled": rule.enabled},
        actor=principal,
    )
    states = await alert_store.states_by_rule(session)
    return _rule_out(rule, states.get(rule.id))


@app.post("/alert-rules", response_model=AlertRuleOut, status_code=201)
async def create_alert_rule(
    payload: AlertRuleIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> AlertRuleOut:
    return await _save_rule(payload, x_dms_principal, session, None)


@app.put("/alert-rules/{rule_id}", response_model=AlertRuleOut)
async def update_alert_rule(
    rule_id: str,
    payload: AlertRuleIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> AlertRuleOut:
    """A changed definition restarts the streaks and the rate; the notified
    level is kept, so an ongoing alarm isn't mailed again."""
    return await _save_rule(payload, x_dms_principal, session, rule_id)


@app.delete("/alert-rules/{rule_id}", status_code=204)
async def delete_alert_rule(
    rule_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _require_monitoring_permission(x_dms_principal)
    try:
        await alert_store.delete_rule(session, rule_id)
    except alert_store.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event("monitoring.alert_rule.deleted", rule_id, {}, actor=x_dms_principal)


@app.get("/downtime-windows", response_model=list[DowntimeWindowOut])
async def list_downtime_windows(
    session: AsyncSession = Depends(get_session),
) -> list[DowntimeWindowOut]:
    return [_window_out(w) for w in await alert_store.list_windows(session)]


async def _save_window(payload: DowntimeWindowIn, principal: str, session, window_id: str | None):
    await _require_monitoring_permission(principal)
    try:
        window = await alert_store.save_window(
            session,
            payload.model_dump(),
            actor=principal,
            now=datetime.now(UTC),
            window_id=window_id,
        )
    except alert_store.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "monitoring.downtime_window.saved", window.id, {"name": window.name}, actor=principal
    )
    return _window_out(window)


@app.post("/downtime-windows", response_model=DowntimeWindowOut, status_code=201)
async def create_downtime_window(
    payload: DowntimeWindowIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> DowntimeWindowOut:
    return await _save_window(payload, x_dms_principal, session, None)


@app.put("/downtime-windows/{window_id}", response_model=DowntimeWindowOut)
async def update_downtime_window(
    window_id: str,
    payload: DowntimeWindowIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> DowntimeWindowOut:
    return await _save_window(payload, x_dms_principal, session, window_id)


@app.delete("/downtime-windows/{window_id}", status_code=204)
async def delete_downtime_window(
    window_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _require_monitoring_permission(x_dms_principal)
    try:
        await alert_store.delete_window(session, window_id)
    except alert_store.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event("monitoring.downtime_window.deleted", window_id, {}, actor=x_dms_principal)
