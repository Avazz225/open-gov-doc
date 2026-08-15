import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_metrics_client import (
    SensorConfigClient,
    bootstrap_http_sensors,
    http_sensor_declarations,
    metrics_payload,
)
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from plugin_orchestration_service import placement, sampler
from plugin_orchestration_service.clients import (
    AuthServiceClient,
    PermissionServiceClient,
    RegistryClient,
)
from plugin_orchestration_service.models import (
    Base,
    ClusterNode,
    PlacementDecision,
    PluginManifest,
    PluginResourceReport,
)
from plugin_orchestration_service.platform_scheduler import (
    KubernetesSchedulerAdapter,
    NullSchedulerAdapter,
    detect_platform_scheduler,
)
from plugin_orchestration_service.schemas import (
    ClusterNodeIn,
    ClusterNodeOut,
    PlacementDecisionOut,
    PlacementRequestIn,
    PluginManifestIn,
    PluginManifestOut,
    ResourceUsageReportIn,
)
from plugin_orchestration_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


async def publish_event(event_type: str, subject: str | None, payload: dict) -> None:
    event = Event(
        event_type=event_type,
        service_name=settings.service_name,
        subject=subject,
        payload=payload,
    )
    await app.state.event_bus.publish(event_type, event.to_bytes())


async def _is_active_superuser(x_dms_principal: str) -> bool:
    active, superuser_principal_id = await app.state.auth_client.get_active_superuser()
    return active and bool(x_dms_principal) and superuser_principal_id == x_dms_principal


async def _require_orchestration_permission(x_dms_principal: str) -> None:
    """Aktiviert die neue domaenengetrennte Admin-Rolle
    `domain-admin-orchestration` (`admin.orchestration`, P10-S1) - der
    aktivierte Superuser (4.6) ist die einzige Ausnahme, gleiches Gate-Muster
    wie license-service/query-service."""
    if await _is_active_superuser(x_dms_principal):
        return
    allowed = bool(x_dms_principal) and await app.state.permission_client.has_permission(
        x_dms_principal, settings.orchestration_permission
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fehlende Domain-Admin-Rolle 'Plugin-Orchestrierung'",
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()

    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS orchestration"))
        await conn.run_sync(Base.metadata.create_all)
        # `create_all` legt fehlende TABELLEN an, aendert aber keine
        # bestehenden - `placement_method` (Plattform-Scheduler-Erkennung,
        # 3.8, P10-S2) kam erst nachtraeglich dazu, gleiches additive
        # Ad-hoc-Migrationsmuster wie z. B. document-service. Idempotent
        # dank IF NOT EXISTS.
        await conn.execute(
            text(
                "ALTER TABLE orchestration.placement_decision "
                "ADD COLUMN IF NOT EXISTS placement_method VARCHAR(32) DEFAULT 'ffd' NOT NULL"
            )
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
    await sensor_config_client.start()
    sensor_config_proxy.bind(sensor_config_client)
    app.state.sensor_config_client = sensor_config_client
    app.state.sensor_registry = sensor_registry

    app.state.auth_client = AuthServiceClient(settings.auth_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)
    app.state.registry_client = RegistryClient(
        settings.registry_service_base_url or "",
        cache_ttl_seconds=settings.registry_dependency_cache_ttl_seconds,
    )
    # Plattform-Scheduler-Erkennung (3.8, P10-S2/P24-S4): `NullSchedulerAdapter`
    # bleibt der Default (Docker-Compose-only-Realitaet dieser
    # Entwicklungsumgebung, P10-S0-Entscheidung) - nur wenn
    # `detect_platform_scheduler()` tatsaechlich Kubernetes erkennt
    # (`KUBERNETES_SERVICE_HOST` gesetzt, also ein echter Pod-Kontext), wird
    # der echte `KubernetesSchedulerAdapter` verdrahtet (siehe ADR 0094).
    detected_platform = detect_platform_scheduler()
    if detected_platform == "kubernetes":
        app.state.scheduler_adapter = KubernetesSchedulerAdapter(
            node_label_selector=settings.kubernetes_node_label_selector
        )
        logger.info("platform_scheduler_active", extra={"platform": detected_platform})
    else:
        app.state.scheduler_adapter = NullSchedulerAdapter()
        if detected_platform is not None:
            logger.warning(
                "platform_scheduler_detected_without_adapter",
                extra={"platform": detected_platform},
            )

    event_bus = NatsEventBusClient(settings.nats_url, stream="orchestration")
    await event_bus.connect()
    app.state.event_bus = event_bus

    sampler_task = asyncio.create_task(sampler.sampler_loop(app.state.session_factory, settings))

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
        sensors=http_sensor_declarations(),
    )

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis)

    yield

    sensor_config_proxy.unbind()
    await app.state.sensor_config_client.stop()
    if registration:
        await registration.stop()
    sampler_task.cancel()
    with suppress(asyncio.CancelledError):
        await sampler_task
    await event_bus.close()
    await app.state.auth_client.close()
    await app.state.permission_client.close()
    await app.state.registry_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)

# Sensor concept (10.1, full rollout): must run at module level, right
# after `app` is constructed - see bootstrap_http_sensors's docstring
# for why this can't move into `lifespan` (FastAPI forbids adding
# middleware once the app has started).
sensor_config_proxy, sensor_registry, _http_requests_sensor, _http_duration_sensor = (
    bootstrap_http_sensors(app, settings.service_name)
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/metrics")
def get_metrics() -> Response:
    body, content_type = metrics_payload(app.state.sensor_registry)
    return Response(content=body, media_type=content_type)


@app.post(
    "/plugins/{plugin_type}", response_model=PluginManifestOut, status_code=status.HTTP_201_CREATED
)
async def upsert_plugin_manifest(
    plugin_type: str,
    payload: PluginManifestIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> PluginManifest:
    """Manifest-Registrierung (3.8) - Upsert je `plugin_type`, kein
    Nebeneinander mehrerer Manifest-Versionen (siehe `models.PluginManifest`
    Docstring)."""
    await _require_orchestration_permission(x_dms_principal)

    now = datetime.now(UTC)
    manifest = await session.get(PluginManifest, plugin_type)
    if manifest is None:
        manifest = PluginManifest(plugin_type=plugin_type, registered_at=now)
        session.add(manifest)
    manifest.version = payload.version
    manifest.scaling_type = payload.scaling_type
    manifest.resource_cpu_cores = payload.resource_cpu_cores
    manifest.resource_ram_mb = payload.resource_ram_mb
    manifest.load_profile = payload.load_profile
    manifest.dependencies = payload.dependencies
    manifest.updated_at = now
    await session.commit()
    await session.refresh(manifest)
    return manifest


@app.get("/plugins", response_model=list[PluginManifestOut])
async def list_plugin_manifests(
    session: AsyncSession = Depends(get_session),
) -> list[PluginManifest]:
    result = await session.execute(select(PluginManifest))
    return list(result.scalars().all())


@app.get("/plugins/{plugin_type}", response_model=PluginManifestOut)
async def get_plugin_manifest(
    plugin_type: str, session: AsyncSession = Depends(get_session)
) -> PluginManifest:
    manifest = await session.get(PluginManifest, plugin_type)
    if manifest is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manifest nicht gefunden")
    return manifest


@app.post("/plugins/{plugin_type}/resource-usage", status_code=status.HTTP_204_NO_CONTENT)
async def report_resource_usage(
    plugin_type: str,
    payload: ResourceUsageReportIn,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Selbstmeldung einer laufenden Plugin-Instanz (analog Registry-
    Heartbeat) - ungegatet, service-zu-service, kein Principal."""
    now = datetime.now(UTC)
    report = await session.get(PluginResourceReport, payload.instance_id)
    if report is None:
        report = PluginResourceReport(instance_id=payload.instance_id, plugin_type=plugin_type)
        session.add(report)
    report.plugin_type = plugin_type
    report.cpu_cores = payload.cpu_cores
    report.ram_mb = payload.ram_mb
    report.reported_at = now
    await session.commit()


@app.get("/nodes", response_model=list[ClusterNodeOut])
async def list_nodes(session: AsyncSession = Depends(get_session)) -> list[ClusterNode]:
    result = await session.execute(select(ClusterNode))
    return list(result.scalars().all())


@app.post("/nodes/{node_id}", response_model=ClusterNodeOut, status_code=status.HTTP_201_CREATED)
async def upsert_node(
    node_id: str,
    payload: ClusterNodeIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> ClusterNode:
    """Kapazitaets-Selbstmeldung eines Knotens (P10-S2) - symmetrisch zu
    `POST /plugins/{type}/resource-usage`: kein echter zweiter Host in
    dieser Docker-Compose-Umgebung ruft das heute auf, aber dieselbe
    Selbstmelde-Logik, die ein kuenftiger zweiter Host nutzen wuerde. Der
    eigene Knoten (`sampler.NODE_ID_SELF`) bleibt weiterhin per `psutil`
    automatisch gesampelt, unabhaengig von diesem Endpunkt."""
    await _require_orchestration_permission(x_dms_principal)

    node = await session.get(ClusterNode, node_id)
    if node is None:
        node = ClusterNode(node_id=node_id)
        session.add(node)
    node.cpu_cores = payload.cpu_cores
    node.total_ram_mb = payload.total_ram_mb
    node.cpu_usage_percent = payload.cpu_usage_percent
    node.available_ram_mb = (
        payload.available_ram_mb if payload.available_ram_mb is not None else payload.total_ram_mb
    )
    node.sampled_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(node)
    return node


@app.post("/placements", response_model=PlacementDecisionOut, status_code=status.HTTP_201_CREATED)
async def create_placement(
    payload: PlacementRequestIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> PlacementDecision:
    await _require_orchestration_permission(x_dms_principal)

    try:
        decision = await placement.decide_placement(
            session,
            payload.plugin_type,
            settings,
            app.state.registry_client,
            app.state.scheduler_adapter,
        )
    except placement.ManifestNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Manifest nicht gefunden"
        ) from exc
    except placement.SingletonAlreadyPlacedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Singleton-Plugin ist bereits platziert",
        ) from exc
    await session.commit()

    await publish_event(
        "orchestration.placement.decided",
        payload.plugin_type,
        {
            "plugin_type": decision.plugin_type,
            "node_id": decision.node_id,
            "placement_allowed": decision.placement_allowed,
            "source": decision.source,
            "placement_method": decision.placement_method,
        },
    )
    return decision


@app.get("/placements", response_model=list[PlacementDecisionOut])
async def list_placements(
    plugin_type: str | None = None, session: AsyncSession = Depends(get_session)
) -> list[PlacementDecision]:
    query = select(PlacementDecision).order_by(PlacementDecision.decided_at.desc())
    if plugin_type is not None:
        query = query.where(PlacementDecision.plugin_type == plugin_type)
    result = await session.execute(query)
    return list(result.scalars().all())
