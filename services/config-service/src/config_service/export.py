"""Builds a `ConfigDocument` (7.3) by aggregating reads from the respective
owner services - config-service has no copy of this data of its own."""

from datetime import UTC, datetime

from config_service.clients import (
    AuthServiceClient,
    MonitoringServiceClient,
    ObjectTypeServiceClient,
    PermissionServiceClient,
    RegistryServiceClient,
    RetentionConfigClient,
    WorkflowServiceClient,
)
from config_service.schemas import (
    AdGroupMappingsExport,
    ApprovalConfigExport,
    BrandingConfigExport,
    BusinessCalendarExport,
    ConfigDocument,
    DeletionReasonCatalogExport,
    DmnDefinitionExport,
    FederationConfigExport,
    ObjectTypeExport,
    ObjectTypeLayoutExport,
    OrgUnitExport,
    OrgUnitRoleLinkExport,
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
            # Only export explicitly saved layouts, not "Smart Layouts" (2.2b)
            # derived generatively from the current attributes
            # - otherwise a reimport would permanently freeze a layout that was
            # deliberately being derived automatically until now.
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
                classification_level=object_type["classification_level"],
                layouts=layouts,
            )
        )
    return result


async def export_workflows(client: WorkflowServiceClient) -> list[WorkflowExport]:
    # Only the respective LATEST version per process family (`GET /process-definitions`
    # without a `name` filter already delivers exactly that) - a reimport is meant to
    # transfer the current state, not reconstruct the
    # complete version history (workflow-service already versions
    # automatically on re-upload anyway, see docs/services/workflow-service.md
    # "Versioning").
    definitions = await client.list_latest_process_definitions()
    result = []
    for definition in definitions:
        detail = await client.get_process_definition(definition["id"])
        result.append(WorkflowExport(name=definition["name"], bpmn_xml=detail["bpmn_xml"]))
    return result


async def export_dmn_definitions(client: WorkflowServiceClient) -> list[DmnDefinitionExport]:
    """Like `export_workflows`: only the respective latest version per DMN family
    (`GET /dmn-definitions` without a `name` filter already delivers exactly that)."""
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


async def export_branding_config(client: RegistryServiceClient) -> BrandingConfigExport:
    config = await client.get_branding_config()
    return BrandingConfigExport(
        product_name=config["product_name"],
        accent_color=config["accent_color"],
        logo_url=config["logo_url"],
    )


async def export_realm_roles(client: AuthServiceClient) -> list[str]:
    return await client.list_realm_roles()


async def export_ad_group_mappings(client: AuthServiceClient) -> AdGroupMappingsExport:
    bundle = await client.get_ad_group_mapping_config()
    return AdGroupMappingsExport(
        mappings=bundle["mappings"],
        composite_rules=bundle["composite_rules"],
        default_role_name=bundle["default_role_name"],
    )


async def export_deletion_reason_catalogs(
    document_client: RetentionConfigClient, folder_client: RetentionConfigClient
) -> DeletionReasonCatalogExport:
    """Union of both owner services' catalogs (P80-S2) - an export is meant
    to capture "what reasons does this installation offer today," and the
    two services' catalogs are expected to be kept in lockstep by this very
    category's own apply logic (`imports.apply_deletion_reason_catalogs`),
    so a union rather than picking one arbitrary owner is the more honest
    round-trip."""
    document_reasons = (await document_client.get_retention_config())["deletion_reason_catalog"]
    folder_reasons = (await folder_client.get_retention_config())["deletion_reason_catalog"]
    merged: list[str] = []
    for reason in document_reasons + folder_reasons:
        if reason not in merged:
            merged.append(reason)
    return DeletionReasonCatalogExport(catalog=merged)


async def export_org_units(client: PermissionServiceClient) -> list[OrgUnitExport]:
    groups = await client.list_groups()
    return [
        OrgUnitExport(name=g["name"], description=g["description"])
        for g in groups
        if g["is_org_unit"]
    ]


async def export_org_unit_role_links(
    client: PermissionServiceClient,
) -> list[OrgUnitRoleLinkExport]:
    groups_by_id = {g["id"]: g for g in await client.list_groups() if g["is_org_unit"]}
    roles_by_id = {r["id"]: r for r in await client.list_roles()}
    assignments = await client.list_role_assignments()
    result = []
    for assignment in assignments:
        if assignment["principal_type"] != "group":
            continue
        group = groups_by_id.get(assignment["principal_id"])
        role = roles_by_id.get(assignment["role_id"])
        if group is None or role is None:
            continue
        result.append(
            OrgUnitRoleLinkExport(
                org_unit_name=group["name"],
                role_name=role["name"],
                resource_id=assignment["resource_id"],
            )
        )
    return result


async def build_export(
    *,
    categories: set[str],
    object_type_client: ObjectTypeServiceClient,
    workflow_client: WorkflowServiceClient,
    permission_client: PermissionServiceClient,
    monitoring_client: MonitoringServiceClient,
    auth_client: AuthServiceClient,
    registry_client: RegistryServiceClient,
    document_client: RetentionConfigClient,
    folder_client: RetentionConfigClient,
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
    if "ad_group_mappings" in categories:
        doc.ad_group_mappings = await export_ad_group_mappings(auth_client)
    if "branding_config" in categories:
        doc.branding_config = await export_branding_config(registry_client)
    if "deletion_reason_catalogs" in categories:
        doc.deletion_reason_catalogs = await export_deletion_reason_catalogs(
            document_client, folder_client
        )
    if "org_units" in categories:
        doc.org_units = await export_org_units(permission_client)
    if "org_unit_role_links" in categories:
        doc.org_unit_role_links = await export_org_unit_role_links(permission_client)
    return doc
