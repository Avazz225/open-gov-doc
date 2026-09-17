from dms_permission_client import PermissionServiceClient

from reporting_service.schemas import ForensicTraceEntry

# Same convention as query-service's own filtering.py (P8-S2) and
# search-service (P5-S4): "document.read" is the generic "may read this
# content" permission. Since Post-Roadmap Phase 39 Session 4 (ADR 0154),
# checked against the entry's own `subject` directly for both `document-
# service` and `folder-service` entries - both are real `ResourceNode`s now
# (previously only folders were, so a document entry's subject had to be
# resolved to its containing folder's `resource_id` via a `DocumentClient`
# lookup first - no longer needed, this module no longer depends on it at
# all).
RESULT_READ_PERMISSION = "document.read"

_RESOLVABLE_SERVICE_NAMES = ("folder-service", "document-service")


def _resolve_resource_ids(entries: list[ForensicTraceEntry]) -> list[str | None]:
    """Resolves a `resource_id` per entry, where possible - 1:1 logic copy
    of `query_service.filtering._resolve_resource_ids`, adapted to operate
    on `ForensicTraceEntry` objects (attribute access) instead of raw event
    dicts, since `_fetch_forensic_trace` already builds typed entries
    before this filter runs. Both `document-service` and `folder-service`
    entries carry their own real `resource_id` directly as `subject`
    (Post-Roadmap Phase 39 Session 4, ADR 0154). Every other `service_name`
    (workflow/case/auth/signature/notification/registry/permission-on-
    non-folder/...) is not resolvable - see docs/services/
    reporting-service.md for the deliberate scope boundary (same one
    query-service already documents)."""
    resolved: list[str | None] = []
    for entry in entries:
        if entry.subject and entry.service_name in _RESOLVABLE_SERVICE_NAMES:
            resolved.append(entry.subject)
        else:
            resolved.append(None)
    return resolved


async def filter_entries_by_permission(
    entries: list[ForensicTraceEntry],
    *,
    principal_id: str,
    permission_client: PermissionServiceClient,
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
    is the only exception. Entries without a resolvable resource are
    hidden fail-closed, instead of inventing a non-existent generic object
    permission for every conceivable domain (workflow/case/auth/...) - same
    boundary query-service's own filtering already draws.

    MUST run before anomaly detection (`forensic.detect_download_anomalies`)
    in the caller - an anomaly computed over events the caller isn't allowed
    to see would itself be an information leak, and would reference events
    absent from the (also-filtered) entries actually shown."""
    if is_superuser:
        return entries
    resource_ids = _resolve_resource_ids(entries)
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
