import asyncio

from query_service.clients import DocumentClient, PermissionServiceClient

# Same convention as search-service (P5-S4): "document.read" is the generic
# "may read this folder's content" permission, checked against the folder's
# `resource_id` - for both document content and actions on the folder
# itself, since only folders are `ResourceNode`s.
RESULT_READ_PERMISSION = "document.read"


async def _resolve_resource_ids(
    events: list[dict], document_client: DocumentClient
) -> list[str | None]:
    """Resolves a folder `resource_id` per event, where possible.
    `document-service` events carry `document_id` as `subject` and must
    first be resolved to their `folder_id`; `folder-service` events already
    carry the `resource_id` directly as `subject`. All other categories
    (workflow/case/auth/signature/notification/registry/permission-on-
    non-folder/...) are not resolvable - see docs/services/query-service.md
    for the deliberate scope boundary."""
    unique_document_ids = list(
        {
            event["subject"]
            for event in events
            if event.get("service_name") == "document-service" and event.get("subject")
        }
    )
    docs = await asyncio.gather(
        *(document_client.get_document(document_id) for document_id in unique_document_ids)
    )
    folder_by_document_id: dict[str, str | None] = {
        document_id: (doc.get("folder_id") or "root") if doc is not None else None
        for document_id, doc in zip(unique_document_ids, docs, strict=True)
    }

    resolved: list[str | None] = []
    for event in events:
        service_name = event.get("service_name")
        subject = event.get("subject")
        if not subject:
            resolved.append(None)
        elif service_name == "folder-service":
            resolved.append(subject)
        elif service_name == "document-service":
            resolved.append(folder_by_document_id.get(subject))
        else:
            resolved.append(None)
    return resolved


async def filter_events_by_permission(
    events: list[dict],
    *,
    principal_id: str,
    permission_client: PermissionServiceClient,
    document_client: DocumentClient,
    is_superuser: bool,
) -> list[dict]:
    """Implements concept 6.1 verbatim: "a query can never see ... more than
    the executing person would be allowed to see anyway". The activated
    superuser (4.6) is the only exception provided for in the concept.
    Events without a resolvable folder resource are hidden fail-closed,
    instead of inventing a non-existent generic object permission for every
    conceivable domain."""
    if is_superuser:
        return events
    resource_ids = await _resolve_resource_ids(events, document_client)
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
