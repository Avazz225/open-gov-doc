# 0206 — P71-S2: Forensic Trace Coverage Bundle

**Status:** accepted
**Context:** P71-S2 (Phase 71, second session, ninth gap-analysis round). The plan's own framing bundled
three sub-items under "forensic-trace audit-coverage": `permission.role.*` events, `folder.viewed`, and
an elevated-priority/severity marker for emergency-shutdown-class events. Verified against the real code
before building — two of the three closed genuine gaps, the third had already been deliberately declined
twice before and was not reopened without new justification.

## What was built

- **`permission.role.*`/`permission.role_assignment.*` events on the direct (non-approval) path.** The
  four-eyes-**approved** execution path (`approval_consumer.py`) already published
  `permission.role.created`/`permission.role.updated`/`permission.role_assignment.created` — but the far
  more common **direct** path (`requires_approval=False`, the out-of-the-box default for these action
  types) never did. `create_role`, `update_role`, and `create_role_assignment` in
  `permission-service/main.py` gained `publish_event` calls on their direct-path branches, matching the
  payload shape the approval path already established. `delete_role_assignment` gained the same treatment
  — a new `permission.role_assignment.deleted` event with no four-eyes precedent to match at all (no
  approval-path branch for deletion exists), attributed via an added, attribution-only
  `X-DMS-Principal` header (the endpoint itself stays ungated, unchanged from before this session).
  `repository.delete_role_assignment` now returns the deleted row (fields captured before
  `session.delete()`, not relying on SQLAlchemy's post-delete attribute-expiry behavior) so the caller has
  a payload to publish.
- **`folder.viewed`**, mirroring `document.viewed`'s established shape: published only on the
  single-item `GET /folders/{id}`, never on the `/children` listing route (avoids event-volume
  explosion, same reasoning as the document-service precedent). Deliberately a **smaller cut** than
  document-service's own mechanism: a single boolean toggle (`AuditTraceConfig.log_viewed`), not a full
  replication of `document-service`'s `AuditTraceConfig` + per-role `AuditTraceRoleOverride` table — the
  plan asked to close "folder read access remains unaudited" (`docs/services/document-service.md:659`),
  not to replicate document-service's full configurability surface, and no operator need for per-role
  folder-view overrides has been identified. New `admin.folder_config` capability
  (`domain-admin-folder-config` role) gates `PUT /audit-trace-config` — folder-service had no existing
  catch-all settings capability of its own (`admin.retention` covers a materially different, unrelated
  concern).

## What was deliberately NOT built: priority/severity marker

Verified before attempting: this exact ask (an elevated audit-priority/severity field for
emergency-shutdown-class events) was already explicitly declined **twice** before — ADR 0023's own
"Consequences" section, ADR 0024, and P63-S1 (which investigated and dropped a near-identical ask citing
those same two ADRs). No new justification was found in this session to override two prior deliberate
architectural decisions. Not built; documented here (by number) so a future gap-analysis round doesn't
propose it a third time without first checking this ADR and the two it cites.

## Verification

`permission-service` 190/190 (+5: `test_create_role_publishes_event`,
`test_update_role_publishes_event`, `test_create_role_assignment_publishes_event`,
`test_delete_role_assignment_publishes_event`,
`test_delete_role_assignment_without_principal_header_still_publishes`). `folder-service` 173/173 (+6:
`test_get_folder_publishes_viewed_event`, `test_list_children_does_not_publish_viewed_event`,
`test_get_folder_does_not_publish_viewed_event_when_disabled`, `test_audit_trace_config_get_and_put`,
`test_put_audit_trace_config_without_permission_is_403`,
`test_put_audit_trace_config_without_principal_is_401`). Both services rebuilt, redeployed, and
live-verified against the real running stack: all five new event types (`permission.role.created`,
`permission.role.updated`, `permission.role_assignment.created`, `permission.role_assignment.deleted`,
`folder.viewed`) confirmed present in `audit-service`'s real hash-chained `GET /events` trail, each with
the correct `actor`/`payload`/`subject`. `audit-service` itself needed zero code changes — it already
subscribes to the `permission.>`/`folder.>` wildcard subjects — and `reporting-service`'s forensic-trace
categorization (`forensic.py`) needed none either, since it categorizes generically by event-type suffix
(`.viewed`→view, etc.), not by an explicit allowlist.

**Real design correction caught mid-session**: `delete_role_assignment`'s repository function originally
deleted the row and returned nothing; the endpoint needed the row's fields (`principal_id`, `role_id`,
`resource_id`) for the event payload after the delete, so the repository function was changed to capture
and return the deleted row instead.

## Consequences

- The role-management and folder-read-access gaps named in the eGov forensic-trace requirement (5.4b)
  are now closed for their default/direct paths, matching what the approval path already had.
- `folder-service` gains its own audit-depth configuration surface, deliberately narrower than
  `document-service`'s — a precedent for "smaller boolean cut is fine when no per-role need is
  identified," not an obligation for future sessions to build out the full document-service shape later
  without a concrete trigger.
- The priority/severity marker's now-three-times-declined status (ADR 0023, ADR 0024, P63-S1, this ADR)
  should be treated as settled unless a genuinely new justification emerges.
