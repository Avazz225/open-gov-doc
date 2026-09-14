import asyncio

from dms_permission_client import PermissionServiceClient

from reporting_service.clients import DocumentClient
from reporting_service.schemas import ForensicTraceEntry

# Same convention as query-service's own filtering.py (P8-S2) and
# search-service (P5-S4): "document.read" is the generic "may read this
# folder's content" permission, checked against the folder's `resource_id`
# - for both document content and actions on the folder itself, since only
# folders are `ResourceNode`s.
RESULT_READ_PERMISSION = "document.read"


async def _resolve_resource_ids(
    entries: list[ForensicTraceEntry], document_client: DocumentClient
) -> list[str | None]:
    """Resolves a folder `resource_id` per entry, where possible - 1:1
    logic copy of `query_service.filtering._resolve_resource_ids`, adapted
    to operate on `ForensicTraceEntry` objects (attribute access) instead
    of raw event dicts, since `_fetch_forensic_trace` already builds typed
    entries before this filter runs. `document-service` entries carry the
    document ID as `subject` and must first be resolved to their
    `folder_id`; `folder-service` entries already carry the resource_id
    directly as `subject`. Every other `service_name` (workflow/case/
    auth/signature/notification/registry/permission-on-non-folder/...) is
    not resolvable - see docs/services/reporting-service.md for the
    deliberate scope boundary (same one query-service already documents)."""
    unique_document_ids = list(
        {
            entry.subject
            for entry in entries
            if entry.service_name == "document-service" and entry.subject
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
    for entry in entries:
        if not entry.subject:
            resolved.append(None)
        elif entry.service_name == "folder-service":
            resolved.append(entry.subject)
        elif entry.service_name == "document-service":
            resolved.append(folder_by_document_id.get(entry.subject))
        else:
            resolved.append(None)
    return resolved


async def filter_entries_by_permission(
    entries: list[ForensicTraceEntry],
    *,
    principal_id: str,
    permission_client: PermissionServiceClient,
    document_client: DocumentClient,
    is_superuser: bool,
) -> list[ForensicTraceEntry]:
    """Row-level RBAC filtering for the forensic trace (5.4b, Post-Roadmap
    Phase 36 Session 3) - closes the gap `docs/services/query-service.md`'s
    own Open Points named: "reporting-service's forensic trace has NO
    row-level result filtering like filtering.py here". Same concept-6.1
    rule query-service's structured queries already enforce ("a query can
    never see... more than the executing person would be allowed to see
    anyway"), applied here to forensic-trace entries instead of raw audit
    events. The base capability check (`reporting.forensic_trace`, ADR
    0072) is unchanged and still gates the endpoint as a whole; this is an
    ADDITIONAL, row-level layer on top of it. The activated superuser (4.6)
    is the only exception. Entries without a resolvable folder resource are
    hidden fail-closed, instead of inventing a non-existent generic object
    permission for every conceivable domain (workflow/case/auth/...) - same
    boundary query-service's own filtering already draws.

    MUST run before anomaly detection (`forensic.detect_download_anomalies`)
    in the caller - an anomaly computed over events the caller isn't allowed
    to see would itself be an information leak, and would reference events
    absent from the (also-filtered) entries actually shown."""
    if is_superuser:
        return entries
    resource_ids = await _resolve_resource_ids(entries, document_client)
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
        entry
        for entry, resource_id in zip(entries, resource_ids, strict=True)
        if resource_id is not None and allowed.get(resource_id, False)
    ]
