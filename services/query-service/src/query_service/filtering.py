from query_service.clients import PermissionServiceClient

# Same convention as search-service (P5-S4): "document.read" is the generic
# "may read this content" permission. Since Post-Roadmap Phase 39 Session 4
# (ADR 0154), checked against the EVENT SUBJECT's own `resource_id` directly
# for both `document-service` and `folder-service` events - both are real
# `ResourceNode`s now (previously only folders were, so a document event's
# subject had to be resolved to its containing folder's `resource_id` via a
# `document_client` lookup first - no longer needed, this module no longer
# depends on `DocumentClient` at all.
RESULT_READ_PERMISSION = "document.read"

_RESOLVABLE_SERVICE_NAMES = ("folder-service", "document-service")


def _resolve_resource_ids(events: list[dict]) -> list[str | None]:
    """Resolves a `resource_id` per event, where possible. Both
    `document-service` and `folder-service` events carry their own real
    `resource_id` directly as `subject` (Post-Roadmap Phase 39 Session 4,
    ADR 0154 - previously only `folder-service` events did, `document-
    service` events needed an extra lookup to their containing folder).
    All other categories (workflow/case/auth/signature/notification/
    registry/permission-on-non-folder/...) are not resolvable - see
    docs/services/query-service.md for the deliberate scope boundary."""
    resolved: list[str | None] = []
    for event in events:
        service_name = event.get("service_name")
        subject = event.get("subject")
        if subject and service_name in _RESOLVABLE_SERVICE_NAMES:
            resolved.append(subject)
        else:
            resolved.append(None)
    return resolved


async def filter_events_by_permission(
    events: list[dict],
    *,
    principal_id: str,
    permission_client: PermissionServiceClient,
    is_superuser: bool,
) -> list[dict]:
    """Implements concept 6.1 verbatim: "a query can never see ... more than
    the executing person would be allowed to see anyway". The activated
    superuser (4.6) is the only exception provided for in the concept.
    Events without a resolvable resource are hidden fail-closed, instead of
    inventing a non-existent generic object permission for every
    conceivable domain."""
    if is_superuser:
        return events
    resource_ids = _resolve_resource_ids(events)
    unique_resource_ids = {resource_id for resource_id in resource_ids if resource_id is not None}
    if not unique_resource_ids:
        return []
    allowed = await permission_client.check_batch(
        principal_id=principal_id,
        permission=RESULT_READ_PERMISSION,
        access_type="read",
        resource_ids=list(unique_resource_ids),
    )
    return [
        event
        for event, resource_id in zip(events, resource_ids, strict=True)
        if resource_id is not None and allowed.get(resource_id, False)
    ]
