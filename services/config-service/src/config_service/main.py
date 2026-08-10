import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from dms_common import configure_logging
from dms_registry_client import maybe_start_registration
from fastapi import FastAPI, Header, HTTPException, Query, status

from config_service import compare, export, imports, migrations
from config_service.clients import (
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
    ImportResult,
)
from config_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

_CONFIG_ADMIN_PRINCIPAL_ID = "config-service"
# Beide Domain-Admin-Rollen, die die von diesem Service angesprochenen
# Schreib-Endpunkte verlangen (workflow-service: admin.object_config,
# monitoring-service: admin.monitoring) - siehe `_ensure_bootstrap_permissions`.
_REQUIRED_ROLE_NAMES = ("domain-admin-config", "domain-admin-monitoring")


def _resolve_categories(categories: list[str] | None) -> set[str]:
    if not categories:
        return set(CATEGORIES)
    unknown = set(categories) - set(CATEGORIES)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unbekannte Kategorien: {sorted(unknown)}")
    return set(categories)


async def _ensure_bootstrap_permissions() -> None:
    """`config-service` braucht `admin.object_config` (Workflow-Uploads) UND
    `admin.monitoring` (Sensor-Konfiguration), um Importe tatsächlich
    anwenden zu können - idempotente Selbstzuweisung beim Start, gleiches
    Bootstrap-Muster wie `migration-service`s `_ensure_config_admin_permission`
    (P12-S2)."""
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
    in dieser Installation), zentral ein Konfigurationspaket zu provisionieren."""
    return bool(settings.fleet_agent_api_key) and authorization == (
        f"Bearer {settings.fleet_agent_api_key}"
    )


async def _require_import_permission(
    x_dms_principal: str, authorization: str | None = None
) -> None:
    if _is_fleet_agent(authorization):
        return
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
    await _ensure_bootstrap_permissions()

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
    await app.state.object_type_client.close()
    await app.state.workflow_client.close()
    await app.state.permission_client.close()
    await app.state.monitoring_client.close()


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


@app.post("/config/import", response_model=ImportResult)
async def import_config(
    payload: dict,
    categories: list[str] | None = Query(default=None),
    x_dms_principal: str = Header(default=""),
    authorization: str | None = Header(default=None),
) -> ImportResult:
    """Gegated hinter `admin.object_config` (dieselbe Domain-Admin-Capability
    wie workflow-service's Prozessdefinition-Upload) - ein voller
    Konfigurationsimport ist eine Erweiterung derselben Verantwortung, siehe
    `settings.py`. `payload` wird bewusst als rohes `dict` entgegengenommen
    (nicht direkt als `ConfigDocument`), damit `migrations.upgrade_to_current()`
    zuerst auf dem rohen Dict ansetzen kann, bevor die aktuelle Schema-Version
    validiert wird."""
    await _require_import_permission(x_dms_principal, authorization)
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
    )
    return ImportResult(schema_version=doc.schema_version, results=results)
