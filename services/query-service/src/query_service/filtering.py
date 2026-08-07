import asyncio

from query_service.clients import DocumentClient, PermissionServiceClient

# Gleiche Konvention wie search-service (P5-S4): "document.read" ist die
# generische "darf Inhalt dieses Ordners lesen"-Berechtigung, geprueft gegen
# den Ordner-`resource_id` - sowohl fuer Dokumentinhalte als auch fuer
# Aktionen auf dem Ordner selbst, da nur Ordner `ResourceNode`s sind.
RESULT_READ_PERMISSION = "document.read"


async def _resolve_resource_ids(
    events: list[dict], document_client: DocumentClient
) -> list[str | None]:
    """Loest je Ereignis eine Ordner-`resource_id` auf, sofern moeglich.
    `document-service`-Ereignisse tragen die `document_id` als `subject` und
    muessen erst auf ihre `folder_id` aufgeloest werden; `folder-service`-
    Ereignisse tragen die `resource_id` bereits direkt als `subject`. Alle
    anderen Kategorien (workflow/case/auth/signature/notification/registry/
    permission-auf-Nicht-Ordner/...) sind nicht auflösbar - siehe
    docs/services/query-service.md fuer die bewusste Umfangsgrenze."""
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
    """Setzt Konzept 6.1 woertlich um: "eine Query kann nie mehr sehen ...
    als die ausfuehrende Person ohnehin duerfte". Der aktivierte Superuser
    (4.6) ist die einzige im Konzept vorgesehene Ausnahme. Ereignisse ohne
    aufloesbare Ordner-Ressource werden fail-closed ausgeblendet, statt eine
    nicht existierende generische Objekt-Berechtigung fuer jede denkbare
    Domaene zu erfinden."""
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
