import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from dms_common import configure_logging
from dms_eventbus_client import NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import FastAPI, Header, HTTPException, Query, status

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
# Drei Domain-Admin-Rollen, die die von diesem Service angesprochenen
# Schreib-Endpunkte verlangen (workflow-service: admin.object_config,
# monitoring-service: admin.monitoring, seit P17-S1 zusätzlich auth-service:
# admin.user_management für die `realm_roles`-Kategorie, 14.1) - siehe
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
    """`config-service` braucht `admin.object_config` (Workflow-Uploads),
    `admin.monitoring` (Sensor-Konfiguration) UND seit P17-S1
    `admin.user_management` (`realm_roles`-Kategorie, 14.1), um Importe
    tatsächlich anwenden zu können - idempotente Selbstzuweisung beim Start,
    gleiches Bootstrap-Muster wie `migration-service`s
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
    """3a/P13-S2: derselbe installationsweite, optionale
    `settings.fleet_agent_api_key` wie bei `license-service` erlaubt dem
    unabhängig betriebenen `fleet-management-service` (kein Keycloak-Principal
    in dieser Installation), zentral ein Konfigurationspaket zu provisionieren.
    Seit P17-S1 ausschließlich von `_require_fleet_agent`/`POST /config/
    fleet-import` geprüft - `_require_import_permission` (RBAC, `POST /config/
    import`) hat keinen Fleet-Bypass mehr, siehe dortiger Docstring."""
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

    # Seit P17-S3 (4.3/14.2): reiner Konsument, kein eigener Stream
    # (`ensure_stream=False`) - config-service hat nichts Eigenes zu
    # publizieren, es reagiert nur auf permission-services bereits
    # bestehendes `permission.approval.approved`-Event, um einen per
    # Vier-Augen-Prinzip zurückgestellten `config.import` nach Genehmigung
    # anzuwenden (siehe consumer.py).
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
    )

    logger.info("config_service_startup_completed")
    yield

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


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


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
    """Delta-/Vergleichsfunktion (7.5, P14-S1) - rein lesend/diagnostisch,
    ungegated wie `GET /config/export` (verändert nichts, deckt keine
    installationsspezifischen Daten wie Lizenzstand/Registry-Erreichbarkeit
    auf, die ohnehin kein Teil von `ConfigDocument` sind). Fehlt `base`, wird
    der eigene aktuelle Live-Export als Basisinstanz verwendet - Anwendungsfall
    "was würde sich ändern, wenn ich `compare` importiere"."""
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
    """`payload` wird bewusst als rohes `dict` entgegengenommen (nicht direkt
    als `ConfigDocument`), damit `migrations.upgrade_to_current()` zuerst auf
    dem rohen Dict ansetzen kann, bevor die aktuelle Schema-Version validiert
    wird. Gemeinsame Anwendungslogik für `POST /config/import` (RBAC),
    `POST /config/fleet-import` (Fleet-Agent-Schlüssel, P17-S1) und - seit
    P17-S3 - `consumer.py`s Nachvollzug eines per Vier-Augen-Prinzip
    genehmigten `config.import` (4.3/14.2) - alle drei Zugriffswege wenden
    dasselbe Dokument identisch an, nur die Authentisierung/der Auslöser
    unterscheidet sich."""
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
    """Gegated hinter `admin.object_config` (dieselbe Domain-Admin-Capability
    wie workflow-service's Prozessdefinition-Upload) - ein voller
    Konfigurationsimport ist eine Erweiterung derselben Verantwortung, siehe
    `settings.py`. Seit P17-S1 KEIN öffentlicher Gateway-Pfad mehr (siehe
    `gateway_service.settings.public_routes`) - der Gateway validiert hier
    also einen echten Keycloak-Bearer-Token und setzt `X-DMS-Principal`
    korrekt (vorher, als dieser Pfad noch öffentlich war, blieb der Header für
    JEDEN Aufruf leer, auch für echte eingeloggte Admins - der RBAC-Zweig war
    faktisch unerreichbar, siehe ADR zu P17-S1). Der Fleet-Agent-Zugriffsweg
    lebt seitdem getrennt unter `POST /config/fleet-import` (reines RBAC hier,
    kein Fleet-Bypass mehr).

    Seit P17-S3 zusätzlich optional per generischem Vier-Augen-Mechanismus
    gegated (4.3, `config.import`) - 14.2 nennt "Konfigurationsimport"
    wörtlich als sensiblen Aktionstyp für die Vier-Augen-Vorbelegung des
    eGov-Pakets. Per Default (keine Konfiguration) bleibt das Verhalten
    unverändert: sofortige Anwendung. `POST /config/fleet-import` bleibt
    bewusst ungegated - der automatisierte, kopflose Provisionierungspfad des
    Fleet-Agents hat kein Mensch-im-Loop, der einen später ausstehenden
    Freigabe-Request sinnvoll bestätigen könnte (ADR 0037)."""
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
    """Dedizierter Pfad für den installationsunabhängigen
    `fleet-management-service` (3a/P13-S2, ADR 0037) - dieser Pfad bleibt am
    Gateway öffentlich (`gateway-service.settings.public_routes`, kein
    Keycloak-Principal in dieser Installation), authentisiert stattdessen
    ausschließlich über `Authorization: Bearer <DMS_FLEET_AGENT_API_KEY>`.
    Seit P17-S1 von `POST /config/import` getrennt - vorher teilten sich
    beide Zugriffswege denselben öffentlichen Pfad, wodurch der Gateway für
    JEDEN Aufruf keinen Bearer-Token validierte und admin-ui-Aufrufer nie
    autorisiert werden konnten."""
    await _require_fleet_agent(authorization)
    return await _apply_config_document(payload, categories)
