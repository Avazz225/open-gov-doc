import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from dms_common import configure_logging
from dms_eventbus_client import NatsEventBusClient
from dms_metrics_client import (
    SensorConfigClient,
    bootstrap_http_sensors,
    http_sensor_declarations,
    metrics_payload,
)
from dms_registry_client import maybe_start_registration
from fastapi import FastAPI, Header, HTTPException, Query, Response, status

from config_service import compare, consumer, export, imports, migrations
from config_service.approval_client import ApprovalClient
from config_service.clients import (
    AuthServiceClient,
    MonitoringServiceClient,
    ObjectTypeServiceClient,
    PermissionServiceClient,
    WorkflowServiceClient,
)
from config_service.schemas import (
    CATEGORIES,
    CompareRequest,
    CompareResult,
    ConfigDocument,
    ImportActionResult,
    ImportResult,
)
from config_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

_CONFIG_ADMIN_PRINCIPAL_ID = "config-service"
# Three domain admin roles required by the write endpoints this service
# calls (workflow-service: admin.object_config,
# monitoring-service: admin.monitoring, since P17-S1 additionally auth-service:
# admin.user_management for the `realm_roles` category, 14.1) - see
# `_ensure_bootstrap_permissions`.
_REQUIRED_ROLE_NAMES = ("domain-admin-config", "domain-admin-monitoring", "domain-admin-users")


def _resolve_categories(categories: list[str] | None) -> set[str]:
    if not categories:
        return set(CATEGORIES)
    unknown = set(categories) - set(CATEGORIES)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unbekannte Kategorien: {sorted(unknown)}")
    return set(categories)


async def _ensure_bootstrap_permissions() -> None:
    """`config-service` needs `admin.object_config` (workflow uploads),
    `admin.monitoring` (sensor configuration) AND, since P17-S1,
    `admin.user_management` (`realm_roles` category, 14.1), to actually be
    able to apply imports - idempotent self-assignment at startup,
    the same bootstrap pattern as `migration-service`'s
    `_ensure_config_admin_permission` (P12-S2)."""
    async with httpx.AsyncClient(
        base_url=settings.permission_service_base_url, timeout=10.0
    ) as client:
        roles = (await client.get("/roles")).json()
        existing_assignments = (
            await client.get(
                "/role-assignments", params={"principal_id": _CONFIG_ADMIN_PRINCIPAL_ID}
            )
        ).json()
        assigned_role_ids = {a["role_id"] for a in existing_assignments}
        for role_name in _REQUIRED_ROLE_NAMES:
            role = next((r for r in roles if r["name"] == role_name), None)
            if role is None:
                logger.warning("config_service_bootstrap_role_missing: %s", role_name)
                continue
            if role["id"] in assigned_role_ids:
                continue
            response = await client.post(
                "/role-assignments",
                json={
                    "principal_type": "service",
                    "principal_id": _CONFIG_ADMIN_PRINCIPAL_ID,
                    "role_id": role["id"],
                    "resource_id": "root",
                },
            )
            response.raise_for_status()


def _is_fleet_agent(authorization: str | None) -> bool:
    """3a/P13-S2: the same installation-wide, optional
    `settings.fleet_agent_api_key` as in `license-service` lets the
    independently operated `fleet-management-service` (no Keycloak principal
    in this installation) centrally provision a configuration package.
    Since P17-S1 checked exclusively by `_require_fleet_agent`/`POST /config/
    fleet-import` - `_require_import_permission` (RBAC, `POST /config/
    import`) no longer has a fleet bypass, see its docstring."""
    return bool(settings.fleet_agent_api_key) and authorization == (
        f"Bearer {settings.fleet_agent_api_key}"
    )


async def _require_import_permission(x_dms_principal: str) -> None:
    allowed = bool(x_dms_principal) and await app.state.permission_client.has_permission(
        x_dms_principal, settings.import_required_capability
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fehlende Domain-Admin-Rolle 'Objekttyp-/Workflow-Konfiguration'",
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.object_type_client = ObjectTypeServiceClient(settings.object_type_service_base_url)
    app.state.workflow_client = WorkflowServiceClient(settings.workflow_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)
    app.state.monitoring_client = MonitoringServiceClient(settings.monitoring_service_base_url)
    app.state.auth_client = AuthServiceClient(settings.auth_service_base_url)
    app.state.approval_client = ApprovalClient(settings.permission_service_base_url)
    await _ensure_bootstrap_permissions()

    sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
    await sensor_config_client.start()
    sensor_config_proxy.bind(sensor_config_client)
    app.state.sensor_config_client = sensor_config_client
    app.state.sensor_registry = sensor_registry

    # Since P17-S3 (4.3/14.2): pure consumer, no stream of its own
    # (`ensure_stream=False`) - config-service has nothing of its own to
    # publish, it only reacts to permission-service's already
    # existing `permission.approval.approved` event, to apply a
    # `config.import` deferred via the four-eyes principle after approval
    # (see consumer.py).
    consumer_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await consumer_bus.connect()
    app.state.consumer_bus = consumer_bus
    await consumer.start_consuming(consumer_bus, settings.approval_subjects, _apply_config_document)

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
        capabilities=["config_export", "config_import"],
        sensors=http_sensor_declarations(),
    )

    logger.info("config_service_startup_completed")
    yield

    sensor_config_proxy.unbind()
    await app.state.sensor_config_client.stop()
    if registration:
        await registration.stop()
    await consumer_bus.close()
    await app.state.object_type_client.close()
    await app.state.workflow_client.close()
    await app.state.permission_client.close()
    await app.state.monitoring_client.close()
    await app.state.auth_client.close()
    await app.state.approval_client.close()


app = FastAPI(title=settings.service_name, lifespan=lifespan)

# Sensor concept (10.1, full rollout): must run at module level, right
# after `app` is constructed - see bootstrap_http_sensors's docstring
# for why this can't move into `lifespan` (FastAPI forbids adding
# middleware once the app has started).
sensor_config_proxy, sensor_registry, _http_requests_sensor, _http_duration_sensor = (
    bootstrap_http_sensors(app, settings.service_name)
)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/metrics")
def get_metrics() -> Response:
    body, content_type = metrics_payload(app.state.sensor_registry)
    return Response(content=body, media_type=content_type)


@app.get("/config/export", response_model=ConfigDocument)
async def export_config(categories: list[str] | None = Query(default=None)) -> ConfigDocument:
    resolved = _resolve_categories(categories)
    return await export.build_export(
        categories=resolved,
        object_type_client=app.state.object_type_client,
        workflow_client=app.state.workflow_client,
        permission_client=app.state.permission_client,
        monitoring_client=app.state.monitoring_client,
        auth_client=app.state.auth_client,
    )


@app.post("/config/compare", response_model=CompareResult)
async def compare_config(payload: CompareRequest) -> CompareResult:
    """Delta/comparison function (7.5, P14-S1) - purely read-only/diagnostic,
    ungated like `GET /config/export` (does not change anything, does not expose
    any installation-specific data such as license state/registry reachability,
    which is not part of `ConfigDocument` anyway). If `base` is missing,
    its own current live export is used as the base instance - use case
    "what would change if I import `compare`"."""
    resolved = _resolve_categories(payload.categories)
    if payload.ignore_regex:
        for pattern in payload.ignore_regex.values():
            try:
                re.compile(pattern)
            except re.error as exc:
                raise HTTPException(
                    status_code=422, detail=f"Ungültige Ignore-Regex: {exc}"
                ) from exc
    base_doc = payload.base
    if base_doc is None:
        base_doc = await export.build_export(
            categories=resolved,
            object_type_client=app.state.object_type_client,
            workflow_client=app.state.workflow_client,
            permission_client=app.state.permission_client,
            monitoring_client=app.state.monitoring_client,
            auth_client=app.state.auth_client,
        )
    categories_result = compare.compare_documents(
        base_doc, payload.compare, categories=resolved, ignore_regex=payload.ignore_regex
    )
    return CompareResult(
        schema_version=payload.compare.schema_version,
        base_exported_at=base_doc.exported_at,
        compare_exported_at=payload.compare.exported_at,
        categories=categories_result,
    )


async def _apply_config_document(payload: dict, categories: list[str] | None) -> ImportResult:
    """`payload` is deliberately accepted as a raw `dict` (not directly
    as `ConfigDocument`), so that `migrations.upgrade_to_current()` can operate
    on the raw dict first, before the current schema version is validated.
    Shared application logic for `POST /config/import` (RBAC),
    `POST /config/fleet-import` (fleet agent key, P17-S1) and - since
    P17-S3 - `consumer.py`'s replay of a `config.import` approved via the
    four-eyes principle (4.3/14.2) - all three access paths apply
    the same document identically, only the authentication/trigger
    differs."""
    resolved = _resolve_categories(categories)
    try:
        upgraded = migrations.upgrade_to_current(payload)
    except migrations.UnsupportedSchemaVersionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    doc = ConfigDocument.model_validate(upgraded)
    results = await imports.apply_import(
        doc,
        categories=resolved,
        object_type_client=app.state.object_type_client,
        workflow_client=app.state.workflow_client,
        permission_client=app.state.permission_client,
        monitoring_client=app.state.monitoring_client,
        auth_client=app.state.auth_client,
    )
    return ImportResult(schema_version=doc.schema_version, results=results)


@app.post("/config/import", response_model=ImportActionResult)
async def import_config(
    payload: dict,
    categories: list[str] | None = Query(default=None),
    x_dms_principal: str = Header(default=""),
) -> ImportActionResult:
    """Gated behind `admin.object_config` (the same domain admin capability
    as workflow-service's process definition upload) - a full
    configuration import is an extension of the same responsibility, see
    `settings.py`. Since P17-S1 NO LONGER a public gateway path (see
    `gateway_service.settings.public_routes`) - the gateway therefore validates
    a real Keycloak bearer token here and sets `X-DMS-Principal`
    correctly (previously, when this path was still public, the header stayed
    empty for EVERY call, even for genuinely logged-in admins - the RBAC branch was
    effectively unreachable, see the ADR for P17-S1). The fleet agent access path
    has since lived separately under `POST /config/fleet-import` (pure RBAC here,
    no more fleet bypass).

    Since P17-S3 additionally optionally gated via the generic four-eyes
    mechanism (4.3, `config.import`) - 14.2 explicitly names "configuration
    import" as a sensitive action type for the four-eyes default
    preset of the eGov package. By default (no configuration), the behavior
    remains unchanged: immediate application. `POST /config/fleet-import` remains
    deliberately ungated - the automated, headless provisioning path of the
    fleet agent has no human in the loop who could meaningfully confirm a
    later pending approval request (ADR 0037)."""
    await _require_import_permission(x_dms_principal)
    if await app.state.approval_client.requires_approval("config.import"):
        request = await app.state.approval_client.create_request(
            action_type="config.import",
            initiated_by=x_dms_principal,
            payload={"document": payload, "categories": categories},
        )
        return ImportActionResult(status="pending_approval", approval_request_id=request["id"])

    result = await _apply_config_document(payload, categories)
    return ImportActionResult(status="applied", result=result)


async def _require_fleet_agent(authorization: str | None) -> None:
    if not _is_fleet_agent(authorization):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ungültiger oder fehlender Fleet-Agent-Schlüssel",
        )


@app.post("/config/fleet-import", response_model=ImportResult)
async def fleet_import_config(
    payload: dict,
    categories: list[str] | None = Query(default=None),
    authorization: str | None = Header(default=None),
) -> ImportResult:
    """Dedicated path for the installation-independent
    `fleet-management-service` (3a/P13-S2, ADR 0037) - this path remains
    public at the gateway (`gateway-service.settings.public_routes`, no
    Keycloak principal in this installation), authenticating instead
    exclusively via `Authorization: Bearer <DMS_FLEET_AGENT_API_KEY>`.
    Separated from `POST /config/import` since P17-S1 - previously
    both access paths shared the same public path, so the gateway
    validated no bearer token for ANY call, and admin-ui callers could
    never be authorized."""
    await _require_fleet_agent(authorization)
    return await _apply_config_document(payload, categories)
