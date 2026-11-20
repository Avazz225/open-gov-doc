# 0240 — The acting identity comes from the verified principal, everywhere

**Status:** accepted
**Context:** P85-S1 (scoping, no code). The P84+ planning round found that many endpoints take the
acting person from the request: `approved_by`, `released_by`, `deleted_by`, `created_by`, and so on.
ADR 0025 had accepted this as a scaffold habit. The consequences are concrete:
- a user can approve their own four-eyes request under a typed-in name;
- anyone can reject any request;
- force-unlock has no permission check and records whatever name the body says.

The user decided (2026-11-20) to bind the acting identity to the gateway-verified principal
**everywhere**, not only for four-eyes or destructive actions. fleet-management-service already got its
own operator identities in P84-S4 (ADR 0234) and is out of scope here.

## Inventory (method)

An AST pass over every `services/*/src/*/main.py` and `schemas.py` (scripts kept with this session's
notes, not in the repo) listed every route that takes a person from the request. That covers body schema
fields named `*_by`, `actor`, `initiated_by`, `signer_principal_id`, plus the two classes where
`principal_id`/`user_id` *is* the actor (`TaskClaimCreate`, `FavoriteCreate`), and query/form parameters
with the same names.

**70 routes matched.** Not counted as acting identities:
- **Filters:** `GET` routes where the field selects *whose* events to show (audit `GET /events`, query
  `GET /query/events`, reporting `actor`/`group_by`, search `created_by`, mail-connector `routed_by`).
  They stay.
- **cmis-connector:** its `actor` already comes from authentication (`Depends(require_actor)`).
- **Targets:** `principal_id` fields naming *who receives* something (role assignment, group member,
  supervisor, delegation deputy, org-hierarchy grant, teamspace invite, batch-check subject). These are
  data, not the actor.

That leaves **54 write routes**. 11 of them do not read `X-DMS-Principal` at all today; the rest read it
for a permission check and then still record the body's name. 17 services send such fields to other
services (about 70 call sites), and the frontends and add-ins send them in about 85 places.

| Service | Route | Actor field today | Reads `X-DMS-Principal` | Session |
|---|---|---|---|---|
| auth-service | `PUT /user-tracking-config/{principal_id}` | UserTrackingConfigIn.updated_by | yes | S3 |
| case-service | `POST /cases` | CaseCreate.created_by | yes | S3 |
| case-service | `POST /cases/{case_id}/register` | CaseRegisterRequest.registered_by | yes | S3 |
| case-service | `POST /cases/{case_id}/documents` | CaseDocumentAdd.added_by | yes | S3 |
| case-service | `DELETE /cases/{case_id}/documents/{document_id}` | CaseDocumentRemove.removed_by | yes | S3 |
| case-service | `POST /cases/{case_id}/attributes/{attribute_name}/pseudonymize` | PseudonymizeAttributeRequest.pseudonymized_by | yes | S3 |
| case-service | `POST /cases/{case_id}/attributes/{attribute_name}/reveal` | RevealAttributeRequest.revealed_by | yes | S3 |
| document-service | `POST /documents` | query/form created_by | yes | S4 |
| document-service | `POST /documents/from-quarantine-release` | query/form created_by | yes | S4 |
| document-service | `POST /documents/cascade-trash` | CascadeTrashRequest.deleted_by | **no** | S2 |
| document-service | `POST /documents/cascade-restore` | CascadeRestoreRequest.restored_by | **no** | S2 |
| document-service | `POST /documents/{document_id}/register` | DocumentRegisterRequest.registered_by | yes | S4 |
| document-service | `POST /documents/{document_id}/promote` | DocumentPromoteRequest.promoted_by | yes | S4 |
| document-service | `PUT /documents/{document_id}/classification-level` | ClassificationLevelUpdate.changed_by | yes | S4 |
| document-service | `POST /documents/{document_id}/classification-level/declassify` | DeclassifyRequest.changed_by | yes | S4 |
| document-service | `POST /documents/{document_id}/attributes/{attribute_name}/pseudonymize` | PseudonymizeAttributeRequest.pseudonymized_by | yes | S4 |
| document-service | `POST /documents/{document_id}/attributes/{attribute_name}/reveal` | RevealAttributeRequest.revealed_by | yes | S4 |
| document-service | `POST /documents/{document_id}/attributes/{attribute_name}/restore` | RestoreAttributeRequest.restored_by | yes | S4 |
| document-service | `POST /documents/{document_id}/redact` | RedactionRequest.created_by | yes | S4 |
| document-service | `DELETE /documents/{document_id}` | query/form deleted_by | yes | S2 |
| document-service | `POST /documents/{document_id}/trash` | TrashRequest.deleted_by | yes | S2 |
| document-service | `POST /legal-holds` | LegalHoldCreate.set_by | yes | S2 |
| document-service | `POST /legal-holds/{hold_id}/release` | LegalHoldReleaseRequest.released_by | yes | S2 |
| document-service | `POST /records-quarantine` | RecordsQuarantineCreate.set_by | yes | S2 |
| document-service | `POST /records-quarantine/{quarantine_id}/release` | RecordsQuarantineReleaseRequest.released_by | yes | S2 |
| document-service | `POST /documents/{document_id}/versions` | query/form created_by | yes | S4 |
| document-service | `POST /documents/{document_id}/lock` | LockAcquireRequest.locked_by | yes | S4 |
| document-service | `DELETE /documents/{document_id}/lock` | LockReleaseRequest.released_by | yes | S4 |
| document-service | `POST /documents/{document_id}/lock/force-release` | LockForceReleaseRequest.released_by | **no** | S2 |
| favorite-service | `POST /favorites` | FavoriteCreate.user_id | **no** | S4 |
| folder-service | `POST /folders` | FolderCreate.created_by | yes | S4 |
| folder-service | `POST /folders/{folder_id}/trash` | TrashRequest.deleted_by | yes | S2 |
| folder-service | `POST /folders/{folder_id}/attributes/{attribute_name}/pseudonymize` | PseudonymizeAttributeRequest.pseudonymized_by | yes | S4 |
| folder-service | `POST /folders/{folder_id}/attributes/{attribute_name}/reveal` | RevealAttributeRequest.revealed_by | yes | S4 |
| folder-service | `POST /folders/{folder_id}/document-references` | FolderDocumentReferenceAdd.added_by | yes | S4 |
| folder-service | `DELETE /folders/{folder_id}/document-references/{document_id}` | FolderDocumentReferenceRemove.removed_by | yes | S4 |
| folder-service | `POST /legal-holds` | LegalHoldCreate.set_by | yes | S2 |
| folder-service | `POST /legal-holds/{hold_id}/release` | LegalHoldReleaseRequest.released_by | yes | S2 |
| folder-service | `POST /folder-templates` | FolderTemplateCreate.created_by | yes | S4 |
| folder-service | `POST /folder-templates/{template_id}/apply` | FolderTemplateApplyRequest.created_by | yes | S4 |
| migration-service | `POST /inbound/transfers/{transfer_id}/documents` | query/form created_by | **no** | S4 |
| ocr-service | `POST /ocr-results/{ocr_result_id}/reviewed` | OcrResultReviewedIn.reviewed_by | yes | S3 |
| permission-service | `POST /approval-requests` | ApprovalRequestCreate.initiated_by | **no** | S2 |
| permission-service | `POST /approval-requests/{request_id}/approve` | ApprovalDecision.approved_by | **no** | S2 |
| permission-service | `POST /approval-requests/{request_id}/reject` | ApprovalRejection.rejected_by | **no** | S2 |
| permission-service | `POST /scope-locks` | ScopeLockCreate.locked_by | yes | S2 |
| permission-service | `DELETE /scope-locks/{lock_id}` | ScopeLockRelease.released_by | **no** | S2 |
| permission-service | `POST /maintenance-mode/trigger` | MaintenanceModeTrigger.triggered_by | **no** | S2 |
| permission-service | `POST /maintenance-mode/lift` | MaintenanceModeLift.lifted_by | **no** | S2 |
| signature-service | `POST /signatures` | SignatureCreate.signer_principal_id | yes | S3 |
| virus-scan-service | `POST /scan` | query/form created_by | yes | S4 |
| workflow-service | `POST /process-definitions/{process_definition_id}/instances` | ProcessInstanceCreate.created_by | yes | S3 |
| workflow-service | `POST /instances/{instance_id}/tasks/{task_id}/claim` | TaskClaimCreate.principal_id | yes | S3 |
| workflow-service | `POST /instances/{instance_id}/tasks/{task_id}/complete` | TaskCompleteRequest.completed_by | yes | S3 |

## Decision

1. **The acting identity is the request's principal, never a body field.** A helper in
   `libs/dms-common` (`acting_principal()`, also usable as a FastAPI dependency) returns it. Every route
   above records and checks *that* identity.
2. **Who sets the principal:**
   - **User calls:** the gateway, from the verified token's `sub`. It already strips every client-sent
     `X-DMS-*` header (`gateway_service/upstream.py`), so a client can't forge either header below.
   - **Service calls:** the calling service, with its own service name (the existing
     `_SYSTEM_PRINCIPAL_HEADERS` pattern). Backend ports are loopback-bound (ADR 0230 and its P84-S3
     follow-up), so only the gateway and services can set these headers.
3. **On behalf of a user.** When a service acts *for* a user, for example a cascade trash from
   folder-service, or mail-connector ingesting into a mailbox owner's folder, it sends its own name in
   `X-DMS-Principal` **and** the user in a new `X-DMS-On-Behalf-Of` header.
   - `acting_principal()` honors `X-DMS-On-Behalf-Of` **only** when `X-DMS-Principal` is a known
     service principal: a fixed list of the system's service names in `dms-common`. A user can't use it.
   - Audit records both: the service as `actor` and the user as `on_behalf_of`. The column already
     exists, from delegations.
   - The pattern where a service simply forwards the user's principal as `X-DMS-Principal` (as in
     folder-service's `document_client`) stays allowed. In that case nothing is recorded as on-behalf.
4. **Body fields are ignored, not rejected.**
   - During the migration a still-sent body field is ignored, and a mismatch with the acting principal
     is logged as `actor_field_ignored` with the route. It is never a `400`.
   - **Reason for ignoring:** rejecting would break every internal caller and UI in the same instant,
     and an ignored field has no security effect, since the field can no longer name anyone.
   - The fields are removed from the request schemas at the end. Unknown fields stay harmless (pydantic
     ignores them), so old clients keep working.
5. **No principal, no write.** A write route without an acting principal returns `401`.
   - Background jobs act as their service principal.
   - The existing `system:<job>` literals become service principals, for example `system:retention-poll`
     becomes `document-service`. The job's purpose is recorded as the audit `reason`.
6. **Four-eyes specifics:**
   - initiator ≠ approver is compared on acting identities. Only principals entitled to decide the action
     type may approve **or reject**: the action's `required_permission`, falling back to the existing
     approval-inbox rule.
   - Creating an approval request takes `initiated_by` from the acting identity. Services creating one
     for a user use `X-DMS-On-Behalf-Of`, and the user is the initiator.
7. **Signatures:** `signer_principal_id` must equal the acting identity, otherwise `403`. Signing in
   someone else's name is never allowed. A service never signs on behalf.
8. **Federated exception:** migration-service `POST /inbound/transfers/{id}/documents` is called by a
   *peer installation*, authenticated by its signature, not by a local principal. The remote user's name
   is kept as data (`origin_created_by`), and the acting identity is the peer
   (`peer:<installation_id>`).

## Session split (replaces the plan's S2–S4)

- **S2 — Foundation and security-critical routes (19).**
  - `acting_principal()`, the service-principal list and `X-DMS-On-Behalf-Of` in `dms-common`.
  - permission-service: approval create/approve/reject, scope locks, maintenance trigger/lift.
  - document-service: force-release, which also gets a real permission check (the existing
    `document.force_unlock` approval-config capability, or `admin.document_management` as the minimum),
    plus trash, delete, cascade trash/restore, legal holds and records quarantine.
  - folder-service: trash and legal holds.
  - The internal callers of exactly these routes switch to on-behalf.
- **S3 — Workflow, cases, signatures and the rest (12).**
  - workflow-service: instance create, claim, complete.
  - case-service: 6 routes.
  - signature-service, with the signer = acting rule.
  - ocr-service `reviewed`, auth-service user-tracking config.
  - The callers of these routes.
- **S4 — Remaining document/folder routes (23).** Register, promote, classification, declassify,
  pseudonymize, reveal, restore, redact, locks, upload and versions; folder create, references and
  templates; favorite-service `user_id`; virus-scan `/scan`; the federated exception. Plus all remaining
  internal callers.
- **S5 — Clients and cleanup.**
  - The five UIs and both add-ins stop sending actor fields.
  - The fields are dropped from the request schemas, and `actor_field_ignored` logging is removed.
  - All six e2e suites, docs, and the `PROGRESS.md` "Authorization" bullet closed.

Each session: tests that a spoofed body field has no effect and that a missing principal gets `401` on
every touched route, plus the service's suite and ruff.

## Consequences

- One rule instead of a mix: the identity is always the principal, and on-behalf is explicit, limited to
  services, and audited.
- Request schemas lose fields at the end of S5. API clients outside this repo that still send them keep
  working; their values are simply ignored.
- The gateway needs no change: it already strips client `X-DMS-*` headers, including the new one.
- `system:*` literal actors disappear from new audit entries, replaced by service principals with a
  `reason`. Old entries are unchanged.

## Implementation status

- **S2 (done):**
  - `dms-common` `acting.py`;
  - permission-service approval create/approve/reject (reject now entitlement-checked; the initiator
    may withdraw), scope locks and maintenance mode;
  - document-service force-release (new `admin.document_lock`), trash, delete, cascade, legal holds and
    records quarantine;
  - folder-service trash and legal holds;
  - the seven approval clients and folder-service's cascade client switched to on-behalf.

  Events use the existing `on_behalf_of` envelope field with the same meaning as for delegations: the
  service as `actor`, the user as `on_behalf_of`. The domain fields (`deleted_by`, `initiated_by`, …)
  name the user.
- **S3 (done):**
  - workflow-service: instance start, claim (always for the caller; `reassign` is how work is handed
    over) and complete (delegation and claim checks on the acting identity);
  - case-service: 6 routes;
  - signature-service: `401` without a principal, `403` for any on-behalf call;
  - ocr-service: reviewer from the acting identity;
  - auth-service: user-tracking config.

  **Addition to the decision:** connector calls act on behalf of the person who completed the preceding
  user task. `complete_task` records them in the reserved process-data key `dms_last_completed_by`, and
  `_handle_connector_task` sends it as `X-DMS-On-Behalf-Of`. This closes the one place where a body
  field was the only way to name the human behind an automated step.

  Callers: migration-service (workflow start on behalf of the transfer initiator) and mail-connector
  (case references as `mail-connector` on behalf of the mailbox user, instead of the non-service
  principal `system:mail-connector`).
- **S4 (done):**
  - **Routes:**
    - document-service: upload, upload from quarantine release and versions (form `created_by` →
      `Form(None)`, ignored); register, promote, classification, declassify; pseudonymize, reveal,
      restore; redact; lock acquire and release;
    - folder-service: create, pseudonymize, reveal, document references, templates;
    - virus-scan `/scan`.
  - **favorite-service:** already compliant (`user_id` = verified `X-DMS-Username`), left keyed by user
    name.
  - **Callers on behalf of the user:** mail-connector, archival-service (documents and cases; the case
    call had been missed in S3), signature-service (the signer's principal instead of a display name),
    teamspace-service, document-service's virus-scan call. ocr-service acts as itself.
  - **Shared helper:** `dms_common.on_behalf_headers(service, actor)`, plus `on_behalf_of=` on the
    connector SDK's `write_document`.
  - **Federated exception, concretized:** the acting identity is `peer:<installation>/<remote creator>`.
    It names both, instead of a separate `origin_created_by` field, so there is no schema change.
  - **Consequence for the UIs:** lock holders and creators are now principals (a user's `sub`), so name
    display moves to P86-S2.

