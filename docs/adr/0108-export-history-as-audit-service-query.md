# 0108 — Export history as an audit-service query, not a dedicated storage mechanism

**Status:** accepted (P28-S2, see `IMPLEMENTATION_PLAN.md`)
**Context:** new post-roadmap feature (PDF export, [ADR 0107](0107-pdf-export-two-pass-merge-subnumbering.md)), affects `services/document-service/`, `services/audit-service/`

## Decision

A document's "export history" — the list the PDF export feature appends
next to the document itself — is not a new, dedicated table or model.
`document-service`'s export endpoint publishes a `document.exported` event
(same shape as the existing `document.downloaded` event published by the
download endpoints), and a new `AuditServiceClient.list_export_history()`
queries `audit-service`'s already-existing, already hash-chained
`GET /events?subject=<document_id>&event_type=document.exported` to build
the history section at export time.

## Rationale

- **`document.downloaded` is the direct precedent**: document-service
  already publishes an access event on every content download
  (`_should_log_document_access`, gated by `AuditTraceConfig`/role
  overrides) and `audit-service` already consumes the entire `document.>`
  subject wildcard into its immutable, hash-chained log
  (`GET /events?subject=&event_type=`). Exporting is structurally the same
  kind of fact ("this document's content left the system at this time, by
  this actor") — reusing the exact same recording mechanism needed no new
  schema, no new consumer wiring, and no new service dependency beyond a
  read-only HTTP query audit-service already supports.
- **Unconditional publish, not gated by `AuditTraceConfig`**: unlike
  `document.downloaded` (whose logging can be turned off per installation/
  role, since it's an optional trace convenience), `document.exported` is
  always published — the export history section IS the feature the user
  asked for, not an optional audit nicety layered on top. Silently omitting
  it because `log_downloaded=False` happens to be configured would make
  "Export" quietly produce an empty history section for no visible reason.
- **A read-only query at export time, not a cached/materialized list**:
  document-service holds no export-history state of its own — every export
  call queries audit-service fresh. Simpler than keeping a second,
  eventually-consistent copy in sync, and the query is already cheap
  (`subject`+`event_type` are both indexed access patterns audit-service
  already serves for the existing forensic-trace UI).
- **Fail-open on an unreachable audit-service**
  (`AuditServiceClient.list_export_history` catches `httpx.HTTPError` and
  returns `[]`): matches the project's established fail-open convention for
  read-only, non-critical cross-service lookups (e.g.
  `office-addin`'s `WorkflowPanel`, `notification-service`'s recipient
  check for a webhook target) — an export should still succeed with an
  empty history section rather than fail outright because a secondary,
  informational service is temporarily unreachable.

## Consequences

- Export history is only as complete as `audit-service`'s log — if
  `AuditTraceConfig`/role overrides have ever disabled `log_downloaded`
  (irrelevant here) or if a given installation's audit retention has pruned
  old events (audit-service has no pruning today, so not yet a real
  concern, but a future retention policy there would also thin out export
  history retroactively).
- `document.exported`'s payload distinguishes single-document exports
  (`{version_number, history_position}`) from folder-export-triggered ones
  (`{via: "folder_export", folder_export_job_id}`) — both are still
  `document.exported` events on the same document subject, so a later
  export's history section includes entries from both origins
  indiscriminately, which matches the literal request ("die
  Exporthistorie mit aufführen", not "only exports triggered the same way").
- No new ADR-worthy consumer was added to audit-service — `document.>` was
  already a consumed wildcard subject since the very first version of that
  service (concept 3.4).
