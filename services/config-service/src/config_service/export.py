"""Baut ein `ConfigDocument` (7.3) durch Zusammenlesen aus den jeweiligen
Owner-Services - config-service hat keine eigene Kopie dieser Daten."""

from datetime import UTC, datetime

from config_service.clients import (
    AuthServiceClient,
    MonitoringServiceClient,
    ObjectTypeServiceClient,
    PermissionServiceClient,
    WorkflowServiceClient,
)
from config_service.schemas import (
    ApprovalConfigExport,
    BusinessCalendarExport,
    ConfigDocument,
    DmnDefinitionExport,
    FederationConfigExport,
    ObjectTypeExport,
    ObjectTypeLayoutExport,
    RoleExport,
    SensorConfigExport,
    WorkflowExport,
)

_LAYOUT_PURPOSES = ("display", "search", "upload")


async def export_object_types(client: ObjectTypeServiceClient) -> list[ObjectTypeExport]:
    types = await client.list_object_types()
    result = []
    for object_type in types:
        layouts = []
        for purpose in _LAYOUT_PURPOSES:
            layout = await client.get_layout(object_type["id"], purpose)
            # Nur explizit gespeicherte Layouts exportieren, keine generativ
            # aus den aktuellen Attributen abgeleiteten "Smart Layouts" (2.2b)
            # - sonst würde ein Reimport ein bislang bewusst automatisch
            # abgeleitetes Layout dauerhaft einfrieren.
            if layout.get("is_custom"):
                layouts.append(
                    ObjectTypeLayoutExport(
                        purpose=purpose,
                        rows=layout["rows"],
                        responsive_breakpoint_px=layout["responsive_breakpoint_px"],
                    )
                )
        result.append(
            ObjectTypeExport(
                name=object_type["name"],
                applies_to=object_type["applies_to"],
                attributes=object_type["attributes"],
                naming_constraints=object_type["naming_constraints"],
                conditions=object_type["conditions"],
                allowed_parent_types=object_type["allowed_parent_types"],
                icon=object_type["icon"],
                kennzeichen_format=object_type["kennzeichen_format"],
                kennzeichen_display_override=object_type["kennzeichen_display_override"],
                required_signature_level=object_type["required_signature_level"],
                default_retention_days=object_type["default_retention_days"],
                deletion_reason_required_override=object_type["deletion_reason_required_override"],
                default_archive_after_days=object_type["default_archive_after_days"],
                archive_encryption_enabled=object_type["archive_encryption_enabled"],
                is_classified=object_type["is_classified"],
                layouts=layouts,
            )
        )
    return result


async def export_workflows(client: WorkflowServiceClient) -> list[WorkflowExport]:
    # Nur die jeweils NEUESTE Version je Prozessfamilie (`GET /process-definitions`
    # ohne `name`-Filter liefert bereits genau das) - ein Reimport soll den
    # aktuellen Stand übertragen, nicht die komplette Versionshistorie
    # rekonstruieren (workflow-service versioniert beim Wiederhochladen
    # ohnehin automatisch neu, siehe docs/services/workflow-service.md
    # "Versionierung").
    definitions = await client.list_latest_process_definitions()
    result = []
    for definition in definitions:
        detail = await client.get_process_definition(definition["id"])
        result.append(WorkflowExport(name=definition["name"], bpmn_xml=detail["bpmn_xml"]))
    return result


async def export_dmn_definitions(client: WorkflowServiceClient) -> list[DmnDefinitionExport]:
    """Wie `export_workflows`: nur die jeweils neueste Version je DMN-Familie
    (`GET /dmn-definitions` ohne `name`-Filter liefert bereits genau das)."""
    definitions = await client.list_latest_dmn_definitions()
    result = []
    for definition in definitions:
        detail = await client.get_dmn_definition(definition["id"])
        result.append(DmnDefinitionExport(name=definition["name"], dmn_xml=detail["dmn_xml"]))
    return result


async def export_business_calendars(client: WorkflowServiceClient) -> list[BusinessCalendarExport]:
    calendars = await client.list_business_calendars()
    return [
        BusinessCalendarExport(
            name=c["name"], non_working_dates=c["non_working_dates"], is_default=c["is_default"]
        )
        for c in calendars
    ]


async def export_roles(client: PermissionServiceClient) -> list[RoleExport]:
    roles = await client.list_roles()
    return [
        RoleExport(name=r["name"], description=r["description"], permissions=r["permissions"])
        for r in roles
    ]


async def export_approval_config(client: PermissionServiceClient) -> list[ApprovalConfigExport]:
    configs = await client.list_approval_configs()
    return [
        ApprovalConfigExport(action_type=c["action_type"], requires_approval=c["requires_approval"])
        for c in configs
    ]


async def export_sensor_config(client: MonitoringServiceClient) -> SensorConfigExport:
    config = await client.get_sensor_config()
    return SensorConfigExport(
        global_default=config["global_default"], overrides=config["overrides"]
    )


async def export_federation_config(client: WorkflowServiceClient) -> FederationConfigExport:
    config = await client.get_federation_config()
    return FederationConfigExport(
        version=config["version"],
        min_compatible_peer_version=config["min_compatible_peer_version"],
    )


async def export_realm_roles(client: AuthServiceClient) -> list[str]:
    return await client.list_realm_roles()


async def build_export(
    *,
    categories: set[str],
    object_type_client: ObjectTypeServiceClient,
    workflow_client: WorkflowServiceClient,
    permission_client: PermissionServiceClient,
    monitoring_client: MonitoringServiceClient,
    auth_client: AuthServiceClient,
) -> ConfigDocument:
    doc = ConfigDocument(exported_at=datetime.now(UTC))
    if "object_types" in categories:
        doc.object_types = await export_object_types(object_type_client)
    if "workflows" in categories:
        doc.workflows = await export_workflows(workflow_client)
    if "dmn_definitions" in categories:
        doc.dmn_definitions = await export_dmn_definitions(workflow_client)
    if "business_calendars" in categories:
        doc.business_calendars = await export_business_calendars(workflow_client)
    if "roles" in categories:
        doc.roles = await export_roles(permission_client)
    if "approval_config" in categories:
        doc.approval_config = await export_approval_config(permission_client)
    if "sensor_config" in categories:
        doc.sensor_config = await export_sensor_config(monitoring_client)
    if "federation_config" in categories:
        doc.federation_config = await export_federation_config(workflow_client)
    if "realm_roles" in categories:
        doc.realm_roles = await export_realm_roles(auth_client)
    return doc
