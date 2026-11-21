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
    RegistryServiceClient,
    RetentionConfigClient,
    WorkflowServiceClient,
)
from config_service.schemas import CategoryResult, ConfigDocument

_LAYOUT_PURPOSES = ("display", "search", "upload")

_OBJECT_TYPE_MUTABLE_FIELDS = (
    "attributes",
    "naming_constraints",
    "conditions",
    "allowed_parent_types",
    "status_transitions",
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
    """Upsert by name. Since P89-S3:
    - **An unchanged role is `skipped`,** not written again. With
      `permission.role.update` under four-eyes (eGov package 1.3.0), every
      re-import of an unchanged package would otherwise open an approval
      request per role.
    - **A write that went to four-eyes is reported, not counted.** Both
      `POST /roles` and `PUT /roles/{id}` answer `pending_approval` when
      gated; this used to count as created/updated, hiding that nothing
      changed yet (same treatment as `apply_org_unit_role_links`)."""
    result = CategoryResult()
    existing = {r["name"]: r for r in await client.list_roles()}
    for entry in entries:
        try:
            match = existing.get(entry.name)
            if match is None:
                action = await client.create_role(
                    name=entry.name, description=entry.description, permissions=entry.permissions
                )
                counter = "created"
            elif match["description"] == entry.description and sorted(
                match["permissions"]
            ) == sorted(entry.permissions):
                result.skipped += 1
                continue
            else:
                action = await client.update_role(
                    match["id"], description=entry.description, permissions=entry.permissions
                )
                counter = "updated"
            if action.get("status") == "pending_approval":
                result.errors.append(
                    f"{entry.name}: wartet auf Genehmigung "
                    f"(approval_request_id={action.get('approval_request_id')})"
                )
                continue
            setattr(result, counter, getattr(result, counter) + 1)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{entry.name}: {exc}")
    return result


async def apply_approval_config(client: PermissionServiceClient, entries: list) -> CategoryResult:
    result = CategoryResult()
    for entry in entries:
        try:
            # `PUT /approval-config/{action_type}` is already an upsert
            # (permission-service creates a missing row as needed).
            # Only the fields the entry contains (P90-S3): a package entry
            # without `required_permission`/`recipient_role` keeps the
            # installation's own values.
            optional = {
                field: getattr(entry, field)
                for field in ("required_permission", "recipient_role")
                if field in entry.model_fields_set
            }
            await client.put_approval_config(
                entry.action_type, requires_approval=entry.requires_approval, **optional
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


async def apply_alert_rules(client: MonitoringServiceClient, entries: list) -> CategoryResult:
    """Upsert by `name` (P88-S7, ADR 0252). Rule names are not unique in
    monitoring-service (the admin page doesn't enforce it); with duplicates,
    the first one in list order is updated and the others are left alone. An
    unchanged rule is `skipped`, not rewritten: monitoring-service restarts a
    rule's streaks and rate on every save, and re-applying a package must not
    reset alarms that are building up."""
    result = CategoryResult()
    existing: dict[str, dict] = {}
    for rule in await client.list_alert_rules():
        existing.setdefault(rule["name"], rule)
    for entry in entries:
        try:
            payload = entry.model_dump()
            match = existing.get(entry.name)
            if match is None:
                existing[entry.name] = await client.create_alert_rule(payload)
                result.created += 1
            elif all(match.get(k) == v for k, v in payload.items()):
                result.skipped += 1
            else:
                await client.update_alert_rule(match["id"], payload)
                result.updated += 1
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{entry.name}: {_error_detail(exc)}")
    return result


async def apply_alert_downtime_windows(
    client: MonitoringServiceClient, entries: list
) -> CategoryResult:
    """Upsert by `name`; `rule_names` are resolved to this installation's rule
    ids, so `alert_rules` is applied first (see `apply_import`). A name with no
    rule is an error for that window, not a silent narrowing: a window meant
    for three rules that would cover two is not what the package said."""
    result = CategoryResult()
    rule_ids_by_name: dict[str, str] = {}
    for rule in await client.list_alert_rules():
        rule_ids_by_name.setdefault(rule["name"], rule["id"])
    existing: dict[str, dict] = {}
    for window in await client.list_downtime_windows():
        existing.setdefault(window["name"], window)
    for entry in entries:
        try:
            payload = entry.model_dump(mode="json")
            rule_names = payload.pop("rule_names")
            if rule_names is None:
                payload["rule_ids"] = None
            else:
                missing = [n for n in rule_names if n not in rule_ids_by_name]
                if missing:
                    result.errors.append(f"{entry.name}: Alarmregel(n) unbekannt: {missing}")
                    continue
                payload["rule_ids"] = [rule_ids_by_name[n] for n in rule_names]
            match = existing.get(entry.name)
            if match is None:
                await client.create_downtime_window(payload)
                result.created += 1
            else:
                await client.update_downtime_window(match["id"], payload)
                result.updated += 1
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{entry.name}: {_error_detail(exc)}")
    return result


def _error_detail(exc: Exception) -> str:
    """monitoring-service answers an unusable rule with 422 and a German
    `detail`; surfacing that beats httpx's generic status line."""
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            detail = response.json().get("detail")
        except Exception:  # noqa: BLE001 - not JSON, fall back to the status line
            detail = None
        if detail:
            return f"{response.status_code}: {detail}"
    return str(exc)


async def apply_export_config(client: RetentionConfigClient, entry) -> CategoryResult:
    """Singleton (P89-S3), a full replace like `branding_config`. document-
    service's 422 (an unknown template placeholder, `diagonal-center` with a
    non-text stamp) reaches `errors` with its detail."""
    result = CategoryResult()
    try:
        await client.put_export_config(entry.model_dump())
        result.updated += 1
    except Exception as exc:  # noqa: BLE001
        result.errors.append(_error_detail(exc))
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


async def apply_branding_config(client: RegistryServiceClient, entry) -> CategoryResult:
    result = CategoryResult()
    try:
        await client.put_branding_config(
            product_name=entry.product_name,
            accent_color=entry.accent_color,
            logo_url=entry.logo_url,
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


async def apply_ad_group_mappings(client: AuthServiceClient, entry) -> CategoryResult:
    """For the `ad_group_mappings` category (4.4/7.3, Post-Roadmap Phase
    39 Session 3, ADR 0153) - a single bundle call, like
    `apply_federation_config`/`apply_sensor_config` above, since
    `auth-service`'s `POST /ad-group-mapping-config/import` is itself
    already idempotent per item (`ad_group_mapping.mapping_exists`/
    `composite_rule_exists`) and applies the whole bundle atomically."""
    result = CategoryResult()
    try:
        await client.import_ad_group_mapping_config(
            {
                "mappings": [m.model_dump() for m in entry.mappings],
                "composite_rules": [r.model_dump() for r in entry.composite_rules],
                "default_role_name": entry.default_role_name,
            }
        )
        result.updated += len(entry.mappings) + len(entry.composite_rules) + 1
    except Exception as exc:  # noqa: BLE001
        result.errors.append(str(exc))
    return result


async def apply_deletion_reason_catalogs(
    document_client: RetentionConfigClient, folder_client: RetentionConfigClient, entry
) -> CategoryResult:
    """Additive merge (P80-S2), NOT a replace - unlike every other
    upsert-by-name category, there is no natural key to match on for a
    plain string list, and a config PACKAGE (14.1) applying its own
    small set of defaults must not silently delete reasons an
    installation already curated for itself. Fanned out to both owner
    services (ADR 0112: the catalog is duplicated per-service)."""
    result = CategoryResult()
    for label, client in (("document-service", document_client), ("folder-service", folder_client)):
        try:
            current = await client.get_retention_config()
            merged = list(current["deletion_reason_catalog"])
            for reason in entry.catalog:
                if reason not in merged:
                    merged.append(reason)
            await client.put_retention_config(
                deletion_reason_required=current["deletion_reason_required"],
                reminder_lead_days=current["reminder_lead_days"],
                deletion_reason_catalog=merged,
            )
            result.updated += 1
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{label}: {exc}")
    return result


async def apply_org_units(client: PermissionServiceClient, entries: list) -> CategoryResult:
    """Upsert by `name`, like `apply_roles` - an existing group that is not
    yet flagged as an org unit is promoted, never demoted (a config
    package granting `is_org_unit` should not silently strip a flag an
    installation set for an unrelated group of the same name it manages
    itself)."""
    result = CategoryResult()
    existing = {g["name"]: g for g in await client.list_groups()}
    for entry in entries:
        try:
            match = existing.get(entry.name)
            if match is None:
                await client.create_group(
                    name=entry.name, description=entry.description, is_org_unit=True
                )
                result.created += 1
            else:
                if not match["is_org_unit"]:
                    await client.set_group_org_unit(match["id"], True)
                result.updated += 1
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{entry.name}: {exc}")
    return result


async def apply_org_unit_role_links(
    client: PermissionServiceClient, entries: list
) -> CategoryResult:
    """Depends on `org_units` and `roles` already being applied (dispatch
    order in `apply_import` below) - an entry naming a not-yet-existing
    org unit or role is a per-entry error, not an import-aborting
    failure, same best-effort convention as every other category."""
    result = CategoryResult()
    groups_by_name = {g["name"]: g for g in await client.list_groups()}
    roles_by_name = {r["name"]: r for r in await client.list_roles()}
    existing_keys = {
        (a["principal_type"], a["principal_id"], a["role_id"], a["resource_id"])
        for a in await client.list_role_assignments()
    }
    for entry in entries:
        try:
            group = groups_by_name.get(entry.org_unit_name)
            if group is None:
                result.errors.append(f"{entry.org_unit_name}: Organisationseinheit nicht gefunden")
                continue
            role = roles_by_name.get(entry.role_name)
            if role is None:
                result.errors.append(f"{entry.role_name}: Rolle nicht gefunden")
                continue
            key = ("group", group["id"], role["id"], entry.resource_id)
            if key in existing_keys:
                result.skipped += 1
                continue
            action = await client.create_role_assignment(
                principal_type="group",
                principal_id=group["id"],
                role_id=role["id"],
                resource_id=entry.resource_id,
            )
            if action["status"] == "pending_approval":
                # `permission.role_assignment.create` can itself be behind
                # the four-eyes principle (14.2) - a real, live-verified
                # case: a package (like the eGov one) that sets this via
                # its own `approval_config` category AND ships
                # `org_unit_role_links` gets exactly this, since
                # `approval_config` is applied earlier in `apply_import`
                # below. NOT counted as `created` - the assignment does not
                # exist yet, only a pending request does, and silently
                # reporting success here would hide that from whoever reads
                # the import result.
                result.errors.append(
                    f"{entry.org_unit_name}->{entry.role_name}: wartet auf Genehmigung "
                    f"(approval_request_id={action['approval_request_id']})"
                )
                continue
            result.created += 1
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{entry.org_unit_name}->{entry.role_name}: {exc}")
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
    registry_client: RegistryServiceClient,
    document_client: RetentionConfigClient,
    folder_client: RetentionConfigClient,
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
    # `org_units`/`org_unit_role_links` deliberately BEFORE `approval_config`:
    # a package (like the eGov one) can set `permission.role_assignment.
    # create` to `requires_approval: true` in its OWN `approval_config`
    # category - applying that first would make this package's own
    # `org_unit_role_links` entries fall under four-eyes and defer to a
    # pending approval instead of actually taking effect, a self-inflicted
    # footgun a config PACKAGE (14.1, "directly usable after applying it")
    # must not hit against itself.
    if "org_units" in categories and doc.org_units is not None:
        results["org_units"] = await apply_org_units(permission_client, doc.org_units)
    if "org_unit_role_links" in categories and doc.org_unit_role_links is not None:
        results["org_unit_role_links"] = await apply_org_unit_role_links(
            permission_client, doc.org_unit_role_links
        )
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
    if "ad_group_mappings" in categories and doc.ad_group_mappings is not None:
        results["ad_group_mappings"] = await apply_ad_group_mappings(
            auth_client, doc.ad_group_mappings
        )
    if "branding_config" in categories and doc.branding_config is not None:
        results["branding_config"] = await apply_branding_config(
            registry_client, doc.branding_config
        )
    if "deletion_reason_catalogs" in categories and doc.deletion_reason_catalogs is not None:
        results["deletion_reason_catalogs"] = await apply_deletion_reason_catalogs(
            document_client, folder_client, doc.deletion_reason_catalogs
        )
    if "export_config" in categories and doc.export_config is not None:
        results["export_config"] = await apply_export_config(document_client, doc.export_config)
    # `alert_rules` BEFORE `alert_downtime_windows`: windows name their rules.
    if "alert_rules" in categories and doc.alert_rules is not None:
        results["alert_rules"] = await apply_alert_rules(monitoring_client, doc.alert_rules)
    if "alert_downtime_windows" in categories and doc.alert_downtime_windows is not None:
        results["alert_downtime_windows"] = await apply_alert_downtime_windows(
            monitoring_client, doc.alert_downtime_windows
        )
    return results
