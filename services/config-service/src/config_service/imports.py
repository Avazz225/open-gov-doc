"""Applies a `ConfigDocument` (7.3) - upsert by natural key
(name/action type) per category, so that a repeated import (e.g.
staging -> production after a further change) does not create duplicates
every time. Best-effort per entry: a single faulty entry does not abort
the entire import (errors end up in `CategoryResult.errors`)."""

from config_service.clients import (
    AuthServiceClient,
    MonitoringServiceClient,
    ObjectTypeServiceClient,
    PermissionServiceClient,
    WorkflowServiceClient,
)
from config_service.schemas import CategoryResult, ConfigDocument

_LAYOUT_PURPOSES = ("display", "search", "upload")

_OBJECT_TYPE_MUTABLE_FIELDS = (
    "attributes",
    "naming_constraints",
    "conditions",
    "allowed_parent_types",
    "icon",
    "kennzeichen_format",
    "kennzeichen_display_override",
    "required_signature_level",
    "default_retention_days",
    "deletion_reason_required_override",
    "default_archive_after_days",
    "archive_encryption_enabled",
    "classification_level",
)


async def apply_object_types(client: ObjectTypeServiceClient, entries: list) -> CategoryResult:
    result = CategoryResult()
    existing = {t["name"]: t for t in await client.list_object_types()}
    for entry in entries:
        try:
            payload = entry.model_dump()
            layouts = payload.pop("layouts")
            match = existing.get(entry.name)
            if match is None:
                created = await client.create_object_type(payload)
                object_type_id = created["id"]
                result.created += 1
            else:
                update_payload = {k: payload[k] for k in _OBJECT_TYPE_MUTABLE_FIELDS}
                await client.update_object_type(match["id"], update_payload)
                object_type_id = match["id"]
                result.updated += 1
            for layout in layouts:
                await client.put_layout(
                    object_type_id,
                    layout["purpose"],
                    {
                        "rows": layout["rows"],
                        "responsive_breakpoint_px": layout["responsive_breakpoint_px"],
                    },
                )
        except Exception as exc:  # noqa: BLE001 - a single entry must not block the rest
            result.errors.append(f"{entry.name}: {exc}")
    return result


async def apply_workflows(client: WorkflowServiceClient, entries: list) -> CategoryResult:
    result = CategoryResult()
    for entry in entries:
        try:
            # workflow-service already versions automatically on upload under the same
            # name (see docs/services/workflow-service.md
            # "Versioning") - no separate pre-check needed, every import
            # counts as "created" (a new version).
            await client.create_process_definition(name=entry.name, bpmn_xml=entry.bpmn_xml)
            result.created += 1
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{entry.name}: {exc}")
    return result


async def apply_dmn_definitions(client: WorkflowServiceClient, entries: list) -> CategoryResult:
    result = CategoryResult()
    for entry in entries:
        try:
            # Like `apply_workflows`: workflow-service already versions automatically
            # on upload under the same name - every import counts
            # as "created" (a new version).
            await client.create_dmn_definition(name=entry.name, dmn_xml=entry.dmn_xml)
            result.created += 1
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{entry.name}: {exc}")
    return result


async def apply_business_calendars(client: WorkflowServiceClient, entries: list) -> CategoryResult:
    """Upsert by `name` (P14-S5) - unlike `apply_workflows`/
    `apply_dmn_definitions`, NO versioning pattern (see
    `workflow_service.models.BusinessCalendar`), a repeated import
    updates the same calendar instead of creating a new version."""
    result = CategoryResult()
    existing = {c["name"]: c for c in await client.list_business_calendars()}
    for entry in entries:
        try:
            match = existing.get(entry.name)
            if match is None:
                await client.create_business_calendar(
                    name=entry.name,
                    non_working_dates=entry.non_working_dates,
                    is_default=entry.is_default,
                )
                result.created += 1
            else:
                await client.update_business_calendar(
                    match["id"],
                    name=entry.name,
                    non_working_dates=entry.non_working_dates,
                    is_default=entry.is_default,
                )
                result.updated += 1
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{entry.name}: {exc}")
    return result


async def apply_roles(client: PermissionServiceClient, entries: list) -> CategoryResult:
    result = CategoryResult()
    existing = {r["name"]: r for r in await client.list_roles()}
    for entry in entries:
        try:
            match = existing.get(entry.name)
            if match is None:
                await client.create_role(
                    name=entry.name, description=entry.description, permissions=entry.permissions
                )
                result.created += 1
            else:
                await client.update_role(
                    match["id"], description=entry.description, permissions=entry.permissions
                )
                result.updated += 1
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{entry.name}: {exc}")
    return result


async def apply_approval_config(client: PermissionServiceClient, entries: list) -> CategoryResult:
    result = CategoryResult()
    for entry in entries:
        try:
            # `PUT /approval-config/{action_type}` is already an upsert
            # (permission-service creates a missing row as needed).
            await client.put_approval_config(
                entry.action_type, requires_approval=entry.requires_approval
            )
            result.updated += 1
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{entry.action_type}: {exc}")
    return result


async def apply_sensor_config(client: MonitoringServiceClient, entry) -> CategoryResult:
    result = CategoryResult()
    try:
        await client.put_global(entry.global_default)
        result.updated += 1
        for sensor_name, enabled in entry.overrides.items():
            await client.put_override(sensor_name, enabled)
            result.updated += 1
    except Exception as exc:  # noqa: BLE001
        result.errors.append(str(exc))
    return result


async def apply_federation_config(client: WorkflowServiceClient, entry) -> CategoryResult:
    result = CategoryResult()
    try:
        await client.put_federation_config(
            version=entry.version, min_compatible_peer_version=entry.min_compatible_peer_version
        )
        result.updated += 1
    except Exception as exc:  # noqa: BLE001
        result.errors.append(str(exc))
    return result


async def apply_realm_roles(client: AuthServiceClient, names: list[str]) -> CategoryResult:
    """No upsert-by-name logic like `apply_roles` needed -
    `auth-service`'s `POST /realm-roles` is itself already idempotent
    (`create_realm_role(..., skip_exists=True)`, see its
    `ensure_realm_roles`). Still best-effort per name instead of a
    batch call, so that a single invalid name (e.g. an empty string)
    does not fail the entire batch."""
    result = CategoryResult()
    for name in names:
        try:
            await client.create_realm_roles([name])
            result.updated += 1
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{name}: {exc}")
    return result


async def apply_import(
    doc: ConfigDocument,
    *,
    categories: set[str],
    object_type_client: ObjectTypeServiceClient,
    workflow_client: WorkflowServiceClient,
    permission_client: PermissionServiceClient,
    monitoring_client: MonitoringServiceClient,
    auth_client: AuthServiceClient,
) -> dict[str, CategoryResult]:
    results: dict[str, CategoryResult] = {}
    if "object_types" in categories and doc.object_types is not None:
        results["object_types"] = await apply_object_types(object_type_client, doc.object_types)
    # `dmn_definitions` deliberately BEFORE `workflows`: a `businessRuleTask` with
    # `camunda:decisionRef` only resolves if the referenced
    # DMN family already exists in workflow-service (P14-S4, see
    # the spiff_adapter.py module docstring there - DMN must be loaded before the
    # referencing BPMN).
    if "dmn_definitions" in categories and doc.dmn_definitions is not None:
        results["dmn_definitions"] = await apply_dmn_definitions(
            workflow_client, doc.dmn_definitions
        )
    if "workflows" in categories and doc.workflows is not None:
        results["workflows"] = await apply_workflows(workflow_client, doc.workflows)
    if "business_calendars" in categories and doc.business_calendars is not None:
        results["business_calendars"] = await apply_business_calendars(
            workflow_client, doc.business_calendars
        )
    if "roles" in categories and doc.roles is not None:
        results["roles"] = await apply_roles(permission_client, doc.roles)
    if "approval_config" in categories and doc.approval_config is not None:
        results["approval_config"] = await apply_approval_config(
            permission_client, doc.approval_config
        )
    if "sensor_config" in categories and doc.sensor_config is not None:
        results["sensor_config"] = await apply_sensor_config(monitoring_client, doc.sensor_config)
    if "federation_config" in categories and doc.federation_config is not None:
        results["federation_config"] = await apply_federation_config(
            workflow_client, doc.federation_config
        )
    if "realm_roles" in categories and doc.realm_roles is not None:
        results["realm_roles"] = await apply_realm_roles(auth_client, doc.realm_roles)
    return results
