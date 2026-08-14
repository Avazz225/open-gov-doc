# admin-ui

**Responsibility:** Administrative web interface — user/role management, object type editor, registry/service overview, managing multiple installations from a single Admin UI (Concept 8).
**Concept Reference:** 8, 3a, 3.10
**No own Postgres schema** — a pure client-rendered SPA (static export, see [ADR 0006](../adr/0006-user-ui-static-export-spa.md)), no own backend process.

## Location in the Repo

`apps/admin-ui/` — identical pattern to `apps/user-ui` (not under `services/`, see ADR 0006): Next.js/TypeScript, `output: "export"`, served via `nginx`, no Node process at runtime.

## Pages

| Route | Purpose |
|---|---|
| `/login/` | Login (identical flow to the User UI, against the Auth Service of the **active installation** via its gateway) |
| `/` | Landing page with hint text, navigation runs through the sidebar (`AdminShell`) |
| `/users/` | Create/delete users, create roles, create/remove role assignments — since **Post-Roadmap Phase 22 Session 2** additionally create/delete groups + member management, see below |
| `/object-types/` | Create/edit/delete object types via a guided form wizard (`ObjectTypeEditor`, since P5b-S3, since P5e-S3 including file-reference-number-generator format/display override, since **P6-S7** including minimum signature level, 3.10, since **P7-S3** including records-disposal deadline/encryption, 5.6) + form layout designer (`LayoutDesigner`, 2.2b) |
| `/registry/` | All instances registered with the registry including health status |
| `/teamspaces/` | Teamspaces admin overview (`TeamspacesAdmin`, 2.5, since **Post-Roadmap Phase 22 Session 5**, [ADR 0090](../adr/0090-teamspaces-admin-overview.md)) — installation-wide status table of all teamspaces against `teamspace-service`'s new `GET /admin/teamspaces`, behind the capability `admin.teamspace_management` (`RequireCapability` AND a gated sidebar entry, real server-side enforcement), see below |
| `/installations/` | Manage the installation list (create/delete/switch) — since P4-S5 |
| `/ocr-settings/` | Maximum word ceiling/processing batch size/content-type allowlist of the OCR Service (`OcrSettings`, since P5b-S5, allowlist since P5d-S1) |
| `/signature-config/` | Signature levels per connector of the Signature Service (`SignatureConfig`, 3.10, since **Post-Roadmap Phase 22 Session 6**, [ADR 0091](../adr/0091-connector-operational-config-live-editable.md)) — **the first Admin UI integration of `signature-service` at all** |
| `/upload-settings/` | Format allowlist of the Document Service (`UploadSettings`, since P5d-S1) |
| `/storage-guard/` | Storage-device-swap guard status + admin override of the Storage Service (`StorageGuard`, since P5b-S6) |
| `/storage-operational-config/` | Write strategy/quorum/max replication attempts of the Storage Service (`StorageOperationalConfig`, 3.6, since **Post-Roadmap Phase 22 Session 6**, [ADR 0091](../adr/0091-connector-operational-config-live-editable.md)) |
| `/kennzeichen-settings/` | Global default "show reference number before filename" of the Object-Type Service (`KennzeichenSettings`, since P5e-S3) |
| `/retention-settings/` | Installation-wide retention/trash configuration (`RetentionSettings`, 5.2/5.2a, since P7-S1) — two independent sections since P7-S1b: Document Service and Folder Service (own, independently configurable settings) |
| `/deletion-register/` | Read the deletion register (`DeletionRegister`, pure read-only table, 5.2a, since P7-S1) — since P7-S1b shows documents **and** folders together in one table (column "Type") |
| `/archival-transfers/` | Records-disposal transfer status + retrieval (`ArchivalTransfersView`, 5.6, since P7-S3, since **P7-S3b** additionally a second section for cases, since **Post-Roadmap Phase 20 Session 7** additionally a `failed_permanent` filter + restart button in both sections) — pure status table(s) against `archival-service`/`case-service`, see below |
| `/processing-failures/` | "Permanently failed" visibility + manual restart (`ProcessingFailuresView`, since **Post-Roadmap Phase 20 Session 7**, [ADR 0083](../adr/0083-admin-ui-processing-failures-visibility.md)) — three sections (notifications/renditions/OCR results) against `notification-service`/`rendering-service`/`ocr-service`, see below |
| `/superuser/` | Superuser break-glass status + request/approve activation (`SuperuserBreakGlass`, 4.6, since P6-S5) — thin caller of the Permission Service's already-existing generic approval endpoints (P6-S4). Since **P6-S6** additionally a "System-wide emergency shutdown" section (not-shutdown, 4.8): status, trigger form (visible only with the capability `system.not_shutdown.trigger`), lift button (visible only to the currently logged-in, active superuser) |
| `/reports/` | Standard reports (`ReportsView`, 5.4a, since P7-S2b) — four report sections + management of schedulable email runs against the new `reporting-service`, see below |
| `/forensic-trace/` | Forensic trace (`ForensicTraceView`, 5.4b, since P7-S2c) — object-related tracing (user/document/folder), category/time-window filters, anomaly banner, CSV/PDF export, see below |
| `/audit-trace-settings/` | Audit depth for the forensic trace (`AuditTraceSettings`, 5.4b, since P7-S2c) — base configuration + role overrides against the Document Service, see below |
| `/query-console/` | Query & trace console (`QueryConsoleView`, 6.1, since P8-S1, since **P8-S2b** additionally the manipulation section) — structured, RBAC-filtered read access + safety switch/dry run/four-eyes manipulation against `query-service`, see below |
| `/share-link-settings/` | Installation-wide toggle + maximum validity period for the public share link (`ShareLinkSettings`, 4.2a, since P14-S10) — same load/save pattern as `OcrSettings`, against the Document Service |
| `/delegations/` | Installation-wide overview of all delegations (`DelegationsAdmin`, 4.4a, since P14-S11) — creation remains self-service (User UI), here only an overview + admin revocation |
| `/config-packages/` | Load/preview/apply configuration packages (`ConfigPackages`, 14.1, since P17-S1) — **the first Admin UI integration of `config-service` at all**, see below |
| `/approval-settings/` | Generic four-eyes settings (`ApprovalSettings`, 4.3, since **Post-Roadmap Phase 22 Session 3**, [ADR 0089](../adr/0089-approval-settings-ui-config-endpoint-stays-ungated.md)) — toggle per configured action type + form to configure a new one for the first time, see below |

All pages except `/login/` are protected via `RequireAuth` (client-side redirect, no server available for middleware — as with the User UI). `RequireAuth` checks the session of the **active installation**. Since **P6-S5**, `/users/` is additionally protected via `RequireCapability` (domain "user/permission management", 4.6) — see "Authorization" below.

## Group Management (Post-Roadmap Phase 22 Session 2)

New "Groups" section in `UserManagement.tsx` (`/users/`), between the existing role and
role-assignment sections. Uses `permission-service`'s new, admin-creatable groups (see
`docs/services/permission-service.md` "Admin-Creatable Groups"), which supplement the
hardcoded "everyone" group that has existed since Phase 19 Session 2 with real member lists.

- Form for creating (name + description), table of all groups with "show members"/"delete"
  per row. Expanding a row (same expand/collapse pattern as `ProcessDefinitionList`'s
  version history) lazily loads the member list (`GET /groups/{id}/members`), shows a
  mini form for adding (username/principal ID) plus a "remove" button per member.
- A user is added to a group via their `principal_id` (usually the username) — no
  selection from the existing user list, a free text field (the same deliberate simplicity as
  `roleAssignments.username`).
- **No new RBAC special case in the frontend**: visibility of the entire `/users/` page has already
  been gated via `RequireCapability` since P6-S5 (see above); the server-side `403` from `permission-service`
  when the `admin.user_management` capability is missing is the actual enforcement, identical to the
  existing pattern for role creation.

## Four-Eyes Settings (Post-Roadmap Phase 22 Session 3, [ADR 0089](../adr/0089-approval-settings-ui-config-endpoint-stays-ungated.md))

New page `/approval-settings/` (`ApprovalSettings.tsx`) — the first Admin UI integration of the generic
four-eyes mechanism (4.3) itself, previously configurable only via `curl`/direct HTTP calls.

- **Table of already configured action types** (`GET /approval-config`, sorted alphabetically): each
  row shows `action_type`, a checkbox for `requires_approval` (clicking toggles it immediately via `PUT
  /approval-config/{action_type}` and reloads), `required_permission` (if set, otherwise "—") and
  the last change timestamp. **`GET /approval-config` only returns rows that already have at least
  one `PUT` call** — no fixed catalog of all action types that exist in the system (see
  `docs/services/permission-service.md`), so an empty state ("No action types
  configured yet") is a normal initial state, not an error.
- **"Configure new action type" form**: free-text `action_type` (e.g. `document.force_unlock`)
  + `requires_approval` checkbox + optional `required_permission` field, calls the same `PUT`
  endpoint. The only way in the Admin UI to set a previously unconfigured action type for the first time.
- **Important correctness rule when toggling**: `required_permission` is ALWAYS sent along with the
  last-loaded value of the row when the toggle is clicked (never omitted) — otherwise `PUT
  /approval-config/{action_type}` would overwrite the field with `null`, which would e.g. silently delete
  `auth.superuser.activate`'s break-glass role binding (`breakglass.approve`).
- **Deliberately no `RequireCapability` wrapper and no `requiresCapability` on the sidebar entry** — unlike
  `/users/`: `PUT /approval-config/{action_type}` was deliberately left ungated in this session (see
  ADR 0089 for the full rationale, in particular the blast-radius analysis across eight affected
  test suites); a client-side capability gate would fake an enforcement that does not exist server-side —
  the same discipline as `ArchivalTransfersView`'s ungated retrieval button.

## Teamspaces Admin Overview (Post-Roadmap Phase 22 Session 5, [ADR 0090](../adr/0090-teamspaces-admin-overview.md))

The first Admin UI integration of `teamspace-service` at all. `TeamspacesAdmin.tsx` (`/teamspaces/`) —
a pure status table (name, description, created by, member count, created at) against the new
`GET /api/teamspace-service/admin/teamspaces` — installation-wide, independent of own
membership (unlike `GET /teamspaces`, which filters by membership and is not used anywhere
in this app). Deliberately **no** administrative actions (delete/member management) — teamspaces
remain self-service (2.5), this page is pure visibility for an oversight role. Unlike
`/approval-settings/` (P22-S3, deliberately ungated there), this endpoint has real server-side
enforcement (`admin.teamspace_management`) — the page is therefore consistently protected both via
`RequireCapability` and a gated sidebar entry, like `/users/`.

## Layout (P4-S5, user feedback after the first real browser test of the MVP)

Replaces the earlier flat top-nav bar with a classic management dashboard layout (Concept 8):

- **`AdminSidebar`** (left): grouped, individually expandable/collapsible navigation (`sidebar-group` blocks, expand/collapse state remembered per browser in `localStorage`). Groups — "Administration" (users & roles, object types, since P5e-S3 additionally reference-number settings, since **P17-S1** additionally configuration packages — visible only with the capability `admin.object_config`, registry), "Installations", since P5b-S5 "Processing" (OCR settings, since P5d-S1 additionally the format allowlist), since P5b-S6 "Storage" (storage guard), since **P7-S1** "Compliance" (retention settings, deletion register, since **P7-S3** additionally records disposal & archiving), since **P6-S5** "Security" (superuser break-glass, since **P7-S2c** additionally forensic trace and audit depth), since **P7-S2b** "Reports" (standard reports), and since **P8-S1** "Diagnostic Tools" (query console, visible only with the capability `admin.query_console`) — built generically for further groups in future sessions. The manipulation section of the query console (since **P8-S2b**) is additionally hidden within the page itself behind the fine-grained capability `admin.query_console.manipulate` (not just the sidebar entry).
- **`AdminShell`**: header (title, `InstallationSwitcher`, `ThemeSwitcher` since P4-S6, username, log out) + a main area on the right that shows whichever page is selected. Since **P6-S6**, `AdminShell` additionally renders a global `MaintenanceBanner` (not-shutdown, 4.8) as its first child — polls `GET /api/permission-service/maintenance-mode` every 30s, shows a prominent notice above the entire page if maintenance mode is active, otherwise `null`; deliberately stays silent if the Permission Service is unreachable (fail-open, no error message).

## Guided Object Type Editor + Form Layout Designer (2.2/2.2a/2.2b, since P5b-S3)

Replaces the free-text JSON attribute editor from P4-S3 with two separate areas on the same page:

- **`ObjectTypeEditor`**: since **P6-S7** additionally a conditional "Minimum signature level" dropdown (none/SES/AES/QES, visible only for `applies_to="document"`, `required_signature_level`, 3.10) — pure configuration, actual enforcement happens during signing in the Signature Service, see `docs/services/signature-service.md`. A structured attribute builder instead of a JSON textarea (one row per attribute: technical name, type, required flag, plus pattern/minimum/maximum depending on type), checkboxes for `allowedParentTypes` (2.2a: `"$ROOT"` + all existing folder classes except the one currently being edited) and an `icon` selection field (2.2a/2.2b, visible only for `applies_to="folder"`) from a **curated, hardcoded icon set** (`folder`, `folder-open`, `folder-star`, `archive`, `briefcase`, `invoice`, `contract` — a deliberate decision against free SVG upload, see Concept 13 "open points": a curated set fully avoids the active-content security concern for uploaded SVGs named there, at the cost of design freedom). Now also supports **editing** existing object types (`PUT /object-types/{id}`) — `name`/`applies_to` remain server-side immutable and are locked in the form; `naming_constraints`/`conditions` are passed through unchanged on save (no UI for them in this session), so that saving via the new editor does not silently reset them to their default.
- **Display names are a creation-time feature, not a permanently editable attribute property**: labels live in the form layout, not in the attribute schema itself (see [ADR 0014](../adr/0014-form-layout-generated-defaults-not-persisted.md)). Only when **newly creating** an object type does the attribute builder show an additional "display name" field per attribute; if at least one display name differs from the technical name, an initial smart layout (2 attributes per row, like `object_type_service.layout.generate_smart_layout`) with these labels is saved right after creation for all three usage purposes (display/search/upload) (`PUT .../layouts/{purpose}` three times). Without differing labels, the server-generated default remains in place — no unnecessary override. When **editing** an existing object type, the display-name field is deliberately absent — later label adjustments run exclusively via the layout designer, so the object type editor does not overwrite targeted layout adjustments already made there.
- **`LayoutDesigner`** (own area below the object type editor): object type and usage-purpose selection (display/search/upload), shows the current layout including a badge ("automatically generated" vs. "customized", from the API's `is_custom`). Editing exclusively via unambiguous row operations instead of free dragging of individual fields between rows (move row up/down, remove row, create new empty row, move a field within a row left/right, remove a field from the row, add an attribute from the not-yet-used attributes into a specific row) — moving a field between two rows therefore takes two steps (remove, then add in the target row) instead of a single gesture; deliberately decided this way, since this environment has no browser for visual verification of a drag-and-drop interaction (see "Tests" below) and every row operation remains unambiguous and testable with Vitest this way. "Save" (`PUT`) and "reset to generated layout" (`DELETE`, then reload) independently per object type and usage purpose.
- **File-reference-number generator fields** (2.2, since P5e-S3): visible only for `applies_to="document"` — a format-string free-text field (`kennzeichen_format`, placeholder hint as `hint` text) and a tri-state selector for `kennzeichen_display_override` ("use global default"/"always show"/"never show"). Forced to `null` client-side as soon as `applies_to="folder"` is chosen — mirrors the same zero-trust stance as the `icon` field (server-side 422 validation remains the actual safeguard, the frontend only avoids the unnecessary error case).
- **Retention fields** (5.2/5.2a, since P7-S1): unlike the reference-number/signature fields, **visible for both `applies_to` values** (the object type schema applies equally to documents and — since P7-S1b — folders) — a number field "default retention period in days" (`default_retention_days`, empty = no default) and a tri-state selector for `deletion_reason_required_override` ("use global default"/"always required"/"never required").
- **Classified-document classification** (2.5, since P15-S1, multi-level since P17-S2/14.2): visible only for `applies_to="document"` — a `<select>` with the four common German VS classification levels (`VS-NfD`/`VS-VERTRAULICH`/`GEHEIM`/`STRENG GEHEIM`) plus "unclassified". **Until P17-S1 a plain checkbox** (`is_classified: bool`) — replaced, not extended, since the backend field itself became `classification_level: str | null`, see [ADR 0059](../adr/0059-egov-paket-aktenplan-hierarchie-und-mehrstufige-vs-einstufung.md).

## Reference-Number Settings (2.2/8, since P5e-S3)

`KennzeichenSettings` (`/kennzeichen-settings/`) edits the same kind of configuration as `OcrSettings`/`UploadSettings`/`StorageGuard`, but this time against the **Object-Type Service** (`GET`/`PUT /api/object-type-service/kennzeichen-config`): a single checkbox toggle "show reference number before filename" as a global default — deliberately not a catalog of several independent display points (tab title, list prefix individually toggleable), see `PROGRESS.md` "File-Reference-Number Generator" for the rationale of this simplification. Individual document types override this default via the tri-state field in the object type editor. Same "unreachable" empty-state pattern as the other configuration pages for a connection error.

## OCR Settings (3.9, since P5b-S5, [ADR 0016](../adr/0016-ocr-configurability-compose-profile-and-live-settings.md))

`OcrSettings` (`/ocr-settings/`) is the first Admin UI page in this project that edits a **backend runtime configuration** rather than a business object (user, object type, ...) — loads `GET /api/ocr-service/config` and saves changes to the maximum word ceiling (empty = no ceiling), the processing batch size, and (since P5d-S1) a comma-separated content-type allowlist via `PUT` (empty = no restriction, non-empty = only listed content types trigger OCR). Deliberately **no** `ocrEnabled` toggle on this page: whether OCR runs at all is a Docker Compose profile opt-out (the container simply isn't deployed) — an Admin UI cannot start a non-running service at the push of a button. If loading fails with a connection error (no HTTP status code, `ApiError` is not thrown), the page instead shows an explanatory empty state ("unreachable, presumably disabled via a Compose profile") — this is the only visibility of `ocrEnabled=false` in this UI.

## Format Allowlist (3.1/3.6, since P5d-S1)

`UploadSettings` (`/upload-settings/`) edits the same kind of backend runtime configuration as `OcrSettings`, this time for the Document Service: `GET`/`PUT /api/document-service/upload-config`, a comma-separated list of allowed content types (empty = no restriction). The hint text makes explicit that the checked content type is determined from the actual file bytes, not from the header sent by the browser — the allowlist thus correctly applies even when a client sends a wrong/generic content type.

## Storage Guard (3.6, since P5b-S6, [ADR 0017](../adr/0017-storage-device-identity-guard.md))

`StorageGuard` (`/storage-guard/`) shows, per configured target of the Storage Service (`GET /api/storage-service/guard-status`), the last confirmed device ID and a status badge for pending re-replications (`pending_copies > 0` → "being re-replicated"), plus an admin override toggle (`GET`/`PUT /api/storage-service/guard-config`, `allow_degraded_start`). As with `OcrSettings`, there is **no** field for the target set itself (backends/credentials are pure deployment configuration, `DMS_TARGETS`) and **no** inline emergency switch at the moment a start is refused — the override is a proactively set standing policy that only takes effect on the next restart (rationale: ADR 0017, the same zero-change-style deployment/Admin-UI split as with `ocrEnabled`, ADR 0016). If the Storage Service is unreachable (e.g. because a start was just refused), the page shows the same explanatory empty state as `OcrSettings`. **Since P5c-S2**: each row additionally has a "Accept storage device swap" button (`window.confirm` confirmation, `POST /api/storage-service/guard-status/{target_id}/reidentify`) for the correction mechanism for an intentional, legitimate device swap — replaces the previously necessary direct DB correction. **Since Post-Roadmap Phase 22 Session 7** ([ADR 0092](../adr/0092-storage-target-metadata-editable.md)): the previously read-only "object lock" column (since P7-S1) is now a checkbox (`PUT /api/storage-service/guard-status/{target_id}/config`), plus a new second checkbox column "records-disposal target" (`role=archive`) — a click takes effect immediately, no restart needed. Still **no** editor for the target set itself (credentials/structure remain deployment configuration, see ADR 0091/0092 "Rationale").

## Storage Service Operational Parameters (3.6, Post-Roadmap Phase 22 Session 6, [ADR 0091](../adr/0091-connector-operational-config-live-editable.md))

`StorageOperationalConfig` (`/storage-operational-config/`) — a plain form (write-strategy
selection, quorum count, maximum replication attempts) against `GET`/`PUT
/api/storage-service/operational-config`. Unlike `StorageGuard`'s admin override (only takes effect on the
next restart), a change here takes effect **immediately** — the same load/save pattern as
`ShareLinkSettings`. As with `StorageGuard`, the target set itself (credentials, `DMS_TARGETS`)
remains outside this page (pure deployment configuration, see ADR 0091 "Rationale").

## Signature Connectors (3.10, Post-Roadmap Phase 22 Session 6, [ADR 0091](../adr/0091-connector-operational-config-live-editable.md))

`SignatureConfig` (`/signature-config/`) — **the first Admin UI integration of `signature-service`
at all**. Table of all configured connectors (`GET /api/signature-service/signature-config`):
`id`/`type` read-only, three checkboxes per row (SES/AES/QES) for `levels` — a click immediately calls
`PUT /api/signature-service/signature-config` for that connector only and reloads. Blocked
client-side (no API call) from deselecting a connector's last remaining level (`levels` may
per the backend never be empty) — the server-side `422` remains the actual enforcement, this is pure
UX anticipation. As with `StorageOperationalConfig`, the connector *list* itself (`id`/`type`,
`DMS_SIGNATURE_PROVIDERS`) remains outside this page.

## Retention & Deletion Register (5.2/5.2a, since P7-S1)

- **`RetentionSettings`** (`/retention-settings/`) bundles two configurations of the Document Service into one form, same load/save pattern as `UploadSettings`: `GET`/`PUT /api/document-service/retention-config` (`deletion_reason_required`, `reminder_lead_days`, empty = no reminder) and `GET`/`PUT /api/document-service/trash-config` (`restore_period_days`). Empty state for an unreachable `document-service`, same pattern as the other configuration pages.
- **`DeletionRegister`** (`/deletion-register/`) is a pure read-only table via `GET /api/document-service/deletion-register` — timestamp, trigger (`forced_deletion`/`trash_expiry`), reason, triggering principal. No editor, no deleting individual entries (the deletion register is immutable per Concept 5.2a).

Legal hold management itself (set/lift) does **not** happen in the Admin UI, but directly on the document in the User UI (`RetentionPanel`, see `docs/services/user-ui.md`) — consistent with a legal hold being an action on a specific document, not an installation-wide setting.

## Records Disposal & Long-Term Archiving (5.6, since P7-S3)

- **`ArchivalTransfersView`** (`/archival-transfers/`) — a pure status table against the new `archival-service` (`GET /api/archival-service/archival-transfers?status=...`, status filter dropdown): document ID, status (`pending`/`locked`/`copied`/`verified`/`released`/`dehydrated`/`failed`/`failed_permanent` since **Post-Roadmap Phase 20 Session 7** with a German label + badge, error message for `failed`/`failed_permanent`), archive format, encrypted column, "archived at"/"dehydrated at" timestamps. A "retrieve" button appears only for status `released`/`dehydrated` (the only ones with a reliable archive copy), calls `POST .../archival-transfers/{id}/retrieve` after confirmation and reloads the table — same confirm/reload pattern as `StorageGuard`'s "accept storage device swap". Since **Post-Roadmap Phase 20 Session 7** ([ADR 0083](../adr/0083-admin-ui-processing-failures-visibility.md)) additionally a "retry" button (both sections, document AND case) only for `failed_permanent`, calls `POST .../retry` (already present server-side since ADR 0078) — deliberately without a confirmation dialog, since unlike retrieval a restart does not change a live storage copy. **Since Post-Roadmap Phase 22 Session 1**: a new "dispose now" form at the top of the document section (free-text field for the document ID + button) calls `POST /api/document-service/documents/{id}/archive-request` (new `requestDocumentArchive()` API client, wraps `document-service`'s manual trigger already present since P5-S6/5.6, which sets `archive_after` to now). Shows a hint text after success instead of reloading the transfer table immediately — the call itself does **not yet** create an `ArchivalTransfer` row, that only happens on `archival-service`'s next poll tick (default hourly).

## Processing Failure Visibility (Post-Roadmap Phase 20 Session 7, [ADR 0083](../adr/0083-admin-ui-processing-failures-visibility.md))

- **`ProcessingFailuresView`** (`/processing-failures/`) — closes the gap created in ADR 0079/0080: `notification-service`/`rendering-service`/`ocr-service` got retry/backoff + `failed_permanent` there, but no Admin UI integration. Three independent sections (no shared generic hook, same "lightweight duplication instead of abstraction" principle as the poll loops of the associated backend services), each loading exclusively `status=failed_permanent` records of the respective service (`GET .../notifications?status=failed_permanent`, `GET .../renditions?status=failed_permanent`, `GET .../ocr-results?status=failed_permanent`) and offering a "retry" button per row (`POST .../{id}/retry`, already present server-side since ADR 0079/0080). `rendering-service`/`ocr-service`'s list endpoints previously had `document_id` as a required parameter — made optional for this session (see below), `notification-service`'s `GET /notifications` already had the needed `status` filter.
- **Triggering records disposal itself** (object type deadline/manual trigger) does **not** happen here, but via the extended `ObjectTypeEditor` (`default_archive_after_days`/`archive_encryption_enabled`, see above) or directly on the document in the User UI — this page is pure observation/retrieval, no "dispose now" control (the automatic poll loop of `archival-service` already covers the normal case).
- **The role gate for retrieval is enforced server-side** (`archival-service`'s `archive_retrieval_role`, default `dms-admin`, from the gateway-injected `X-DMS-Roles` header) — the Admin UI itself does not hide the button based on role, a click without the matching role returns `403`, shown in the page's `error` text.
- **Second section "Case Records Disposal"** (`CaseArchivalSection`, same `/archival-transfers/` slug, since **P7-S3b**) — no new page slug, same multi-section pattern as `RetentionSettings` (documents + folders in one form). Contains two parts: **(a)** a configuration form for `CaseArchivalConfig` (`GET`/`PUT /api/case-service/case-archival-config`) — installation-wide disposal deadline after closure + encryption toggle, no counterpart in `ObjectTypeEditor`, since cases have no own `applies_to` category (see `docs/services/case-service.md`). **(b)** a status table (`GET /api/archival-service/case-archival-transfers?status=...`) with case ID, status (`pending`/`locked`/`packaged`/`verified`/`released`/`failed`), encrypted column, "archived at". A "download package" button appears only for status `released`, downloads the (optionally server-side decrypted) ZIP package via `GET .../case-archival-transfers/{id}/package` as a blob (`triggerBrowserDownload`, same pattern as `ReportsView`/`ForensicTraceView`) — **no** writing back to a live target as with document retrieval, since a case has no own live storage space.

## Standard Reports (5.4a, since P7-S2b)

`ReportsView` (`/reports/`) bundles four fixed report sections — document volume, open workflow tasks, storage usage per backend, user activity — plus a fifth section for managing schedulable report runs, all against the new `reporting-service` (see `docs/services/reporting-service.md`). Each report section follows the same pattern: filter form (where applicable), table, two export buttons (`GET .../export?format=csv|pdf`, blob download via the same `triggerBrowserDownload` mechanism as the document download in the User UI). The scheduling section (`ReportScheduleSection`) creates/deletes `report_schedule` rows (report type, format, frequency, recipient email) — the actual dispatch (poll loop + email with a download link) runs entirely in `reporting-service`, this page is only the management UI for it. Deliberately in the Admin UI rather than the User UI — system evaluation/user activity is an administrative matter, the same role split as e.g. `MaintenanceBanner`.

## Forensic Trace & Audit Depth (5.4b, since P7-S2c)

- **`ForensicTraceView`** (`/forensic-trace/`) — filter form (actor, object ID, category dropdown `view`/`download`/`change`/`delete`, time window from/to), results table (timestamp/event type/category/actor/object/service), anomaly banner (red as soon as `anomalies` in the response is non-empty), two export buttons (same `triggerBrowserDownload` pattern as `ReportsView`). Every query (including export) sends the currently logged-in username (`useAuth().user.username`) as `queried_by` — a required parameter of `reporting-service`, since every trace query is itself audited there again (self-audit, 5.4b concept requirement). Before the first query, the page deliberately shows its own hint instead of an automatic initial load — unlike `ReportsView`'s standard reports, which load immediately when opened, an auto-load here would trigger an unfiltered, potentially very broad trace plus a self-audit event on every page visit.
- **`AuditTraceSettings`** (`/audit-trace-settings/`) — two sections against the Document Service: base configuration (two checkboxes `log_viewed`/`log_downloaded`, default both on) and a table of role overrides (role, two tri-state selects default/on/off per category, delete button) with a creation form. Both selects in the creation form deliberately carry their own `aria-label` texts, different from the base checkbox labels (`overrideLogViewedLabel`/`overrideLogDownloadedLabel`) — identical accessible names in two places on the same page would have created an unresolvable ambiguity both for screen reader users and for `getByLabelText`-based tests (discovered while writing the tests, see below).

## Query Console (6.1, since P8-S1/P8-S2b)

`QueryConsoleView` (`/query-console/`) — two sections, both against `query-service` (see `docs/services/query-service.md`):

- **Read access** (`QueryEventsSection`, since P8-S1): filter form (actor, object ID, event type, time window from/to), results table (timestamp/event type/actor/object/service). Unlike the forensic trace (`reporting-service`, unfiltered), `query-service` actively filters result rows by the executing person's folder permissions — the page makes this transparent: after every query, a hint shows either `"{visible} of {total} events visible"` or, for the activated superuser, its own "unfiltered" hint. No `queried_by` parameter needed (unlike the forensic trace) — `X-DMS-Principal` is injected by the gateway from the bearer token. **No free-text SQL input field** — `query-service`'s `POST /query` path remains unused anyway without an installed parser plugin (ADR 0031); only the structured filter API (`GET /query/events`) is wired to the UI.
- **Manipulation** (`ManipulationSection`, since **P8-S2b**) — visible only with the fine-grained capability `admin.query_console.manipulate` (in addition to the already-existing sidebar gate, defense in depth — actual enforcement remains server-side in `query-service`). Three subsections:
  - **Safety switch**: status display (active/until when/by whom) + activation form (minute input, default 15)/deactivate button — direct calls to `query-service`'s three safety-switch endpoints.
  - **Action**: `<select>` with the three known `action_type` strings (hardcoded mirror of `manipulation.py`'s catalog), a dynamic parameter form per selection (`document.attribute_reset`: two text fields; `permission.role_assignment.delete`: one number; `object_type.update`: number + field dropdown + raw JSON text field for `value`, parsed client-side before submission). Two-step flow: "simulate" (dry run) shows preview text + a critical badge and holds the `dry_run_token` in component state — only afterward does "execute" appear; any form change discards the token (forces a fresh dry run before every execution). Result `"executed"` shows a success message, `"pending_approval"` shows a hint text and reloads the approval table.
  - **Pending approvals**: `GET /approval-requests?status=pending` against `permission-service` (already-existing, generic four-eyes client function from P6-S5, here called once without an `action_type` filter and filtered client-side to the three known action types — the API only filters `action_type` as a single string, not a list), per row action type/initiator/timestamp/raw parameters (JSON) + "approve" button (`approveApprovalRequest`, already generic from the break-glass integration, reused unchanged). No reject button in this session.

## Public Share Link (4.2a, since P14-S10)

`ShareLinkSettings` (`/share-link-settings/`) — a form against `GET`/`PUT /api/document-service/share-link-config`: checkbox "allow share links" (`enabled`) + number field "maximum validity period for new links (days)" (`max_validity_days`), same load/save/empty-state pattern as `OcrSettings`. Disabling also takes immediate effect on already-issued links (see `docs/services/document-service.md`/[ADR 0047](../adr/0047-public-share-link-query-param-token-and-disable-semantics.md)), not just on new creations — this page itself does not intervene for that, it is server-side logic in `document-service`.

## Delegation During Absence (4.4a, since P14-S11)

`DelegationsAdmin` (`/delegations/`) — a pure table overview against `GET /api/permission-service/delegations` (unfiltered, all delegations installation-wide): represented person, delegate, time window, status (active/not yet started/expired/revoked), revoke button only for active rows. Creating is deliberately not an Admin UI feature (self-service, per the concept's wording "a person can... set up" — see `docs/services/user-ui.md`'s `DelegationsPane`); this page only maps the second, concept-envisioned revocation path ("... or be ended by an authorized admin role"), enforced server-side via `delegation_revoke_admin_role` in `permission-service`, see [ADR 0048](../adr/0048-delegation-lives-in-permission-service-no-task-assignee-retrofit.md).

## Configuration Packages (14.1, since P17-S1)

`ConfigPackages` (`/config-packages/`) — **the first Admin UI integration of `config-service`
at all** (previously exclusively CLI/raw API/fleet agent, see
`docs/services/config-service.md`). Flow:

1. **Load package file**: `<input type="file">`, parsed client-side as JSON via `FileReader`
   (deliberately not `File.text()` — a web standard, but not implemented in the jsdom test
   environment used here, see the component comment). Invalid JSON/missing
   `schema_version` field → error message, no crash.
2. **Show manifest/categories**: name/version/compatibility range/description/origin/
   license from `document.manifest`, if present (optional, see `config-service`), plus a
   list of the categories actually present in the document with entry counts.
3. **Preview** (`compareConfig`, `POST /api/config-service/config/compare` without `base` — automatically
   uses its own current live export as the base instance, 7.5/P14-S1): shows, per
   category, what would be new (`only_in_compare`), what would change (`differing`) and what
   would remain unchanged in the current system (`only_in_base` — import is additive/upsert,
   nothing is deleted here).
4. **Apply** (`importConfig`, `POST /api/config-service/config/import`): a results table with
   newly created/updated/skipped/errored counts per category (`CategoryResult`).
5. **Export current configuration**: a natural addition, `GET /api/config-service/
   config/export` as a JSON file download (`Blob`/`URL.createObjectURL`) — not part of the
   original session scope, but added at no extra cost since the same client code already
   loads a live export for the preview anyway.

Sidebar visibility gated via `requiresCapability: "admin.object_config"` (the same capability
as `config-service`'s own import gate) — no additional `RequireCapability` wrapper on the
page itself, following the more common, already-established pattern in this project (see
"Authorization" below). Details/rationale for the backend changes (manifest field, new
`realm_roles` category, split of `config/import`/`config/fleet-import` at the gateway) see
[ADR 0058](../adr/0058-konfigurationspakete-manifest-realm-roles-and-gateway-import-route-split.md).

## Multiple Installations (Concept 3a/8, since P4-S5)

The Admin UI can manage several fully independent DMS installations, without needing to log in again on every switch — see [ADR 0008](../adr/0008-admin-ui-multi-installation-sessions.md) for the technical rationale. Summary:

- Installation list (`{id, name, gatewayBaseUrl}`) purely client-side in `localStorage`, managed via `InstallationManager` (`/installations/`) and `useInstallation()` (`lib/installation-context.tsx`).
- `InstallationSwitcher` in the header switches the active installation — remains hidden as long as only one installation is configured.
- **Own session per installation**: `auth-context.tsx` stores tokens under `dms.tokens.<installationId>` instead of a single global key. Switching to an installation already logged into once requires no re-login as long as its session is still valid; a new, never-logged-into installation shows the login on switch.
- **No single sign-on across installation boundaries** — deliberate, matches the full isolation from Concept 3a.
- `lib/api.ts`'s gateway address has, since this session, been a mutable module variable (`setGatewayBaseUrl()`) instead of a fixed constant, set synchronously by the `InstallationProvider` on every switch.

## Theming (Concept 8, since P4-S6)

`src/lib/theme-context.tsx` (`ThemeProvider`/`useTheme()`) — identical pattern to the User UI (deliberately duplicated instead of shared, ADR 0006), toggleable via the `ThemeSwitcher` in the header. Stored across devices on the user account of the **active installation** (`GET/PUT /api/auth-service/me/preferences`, `accessToken` comes from the installation-bound `AuthProvider`, ADR 0008), see [ADR 0009](../adr/0009-cross-ui-theming-profile-persistence.md). The `localStorage` cache key (`dms.theme`) is deliberately **not** installation-specific — switching installations briefly still shows the last-cached theme choice, until the new installation's own preference has been loaded (see ADR 0009 "Consequences").

## Backend Integration

Exclusively via the API gateway of the respective **active installation** (3.5, `/api/{service_type}/{path}`):

| Area | Gateway Calls |
|---|---|
| Login/identity | `POST /api/auth-service/login`, `GET /api/auth-service/me` |
| Users | `GET/POST /api/auth-service/users`, `DELETE /api/auth-service/users/{id}` |
| Roles | `GET/POST /api/permission-service/roles` |
| Teamspaces admin overview (since **Post-Roadmap Phase 22 Session 5**) | `GET /api/teamspace-service/admin/teamspaces` |
| Groups (since **Post-Roadmap Phase 22 Session 2**) | `GET/POST /api/permission-service/groups`, `DELETE .../{id}`, `GET/POST /api/permission-service/groups/{id}/members`, `DELETE .../{id}/members/{principal_id}` |
| Role assignments | `GET/POST /api/permission-service/role-assignments`, `DELETE .../{id}` |
| Four-eyes settings (since **Post-Roadmap Phase 22 Session 3**) | `GET /api/permission-service/approval-config`, `PUT .../{action_type}` |
| Object types | `GET/POST/PUT/DELETE /api/object-type-service/object-types`, `GET/PUT/DELETE .../object-types/{id}/layouts/{purpose}` (since P5b-S3) — since **P7-S3** additionally `default_archive_after_days`/`archive_encryption_enabled` in the create/update payload (5.6) |
| Registry | `GET /api/registry-service/instances` |
| Theme preference | `GET/PUT /api/auth-service/me/preferences` (since P4-S6) |
| OCR settings | `GET/PUT /api/ocr-service/config` (since P5b-S5) |
| Storage guard | `GET/PUT /api/storage-service/guard-config`, `GET /api/storage-service/guard-status` (since P5b-S6), `POST /api/storage-service/guard-status/{target_id}/reidentify` (since P5c-S2), `PUT /api/storage-service/guard-status/{target_id}/config` (since **Post-Roadmap Phase 22 Session 7**) |
| Operational parameters (Storage, since **Post-Roadmap Phase 22 Session 6**) | `GET/PUT /api/storage-service/operational-config` |
| Signature connectors (since **Post-Roadmap Phase 22 Session 6**) | `GET/PUT /api/signature-service/signature-config` |
| Not-shutdown / maintenance mode | `GET /api/permission-service/maintenance-mode` (since P6-S6, `MaintenanceBanner` + `SuperuserBreakGlass`), `POST /api/permission-service/maintenance-mode/trigger`, `POST /api/permission-service/maintenance-mode/lift` (both since P6-S6, `SuperuserBreakGlass`) |
| Retention & deletion register | `GET/PUT /api/document-service/retention-config`, `GET/PUT /api/document-service/trash-config`, `GET /api/document-service/deletion-register` (all since P7-S1, `RetentionSettings`/`DeletionRegister`); since **P7-S1b** additionally the same three against `folder-service` (own, independent configs) |
| Standard reports | `GET /api/reporting-service/reports/{document-volume,open-workflow-tasks,storage-usage,user-activity}` + `.../export?format=csv\|pdf`, `GET/POST/DELETE /api/reporting-service/report-schedules`, `GET /api/reporting-service/report-runs/{id}/download` (all since P7-S2b, `ReportsView`) |
| Forensic trace & audit depth | `GET /api/reporting-service/forensic-trace` + `.../export?format=csv\|pdf` (since P7-S2c, `ForensicTraceView`), `GET/PUT /api/document-service/audit-trace-config`, `GET/PUT/DELETE /api/document-service/audit-trace-role-overrides/{role}` (since P7-S2c, `AuditTraceSettings`) |
| Records disposal & archiving | `GET /api/archival-service/archival-transfers?status=...`, `POST .../archival-transfers/{id}/retrieve` (both since P7-S3, `ArchivalTransfersView`); since **P7-S3b** additionally `GET /api/archival-service/case-archival-transfers?status=...`, `GET .../case-archival-transfers/{id}/package`, `GET`/`PUT /api/case-service/case-archival-config` (`CaseArchivalSection`); since **Post-Roadmap Phase 20 Session 7** additionally `POST .../archival-transfers/{id}/retry`, `POST .../case-archival-transfers/{id}/retry`; since **Post-Roadmap Phase 22 Session 1** additionally `POST /api/document-service/documents/{id}/archive-request` |
| Processing failures | `GET /api/notification-service/notifications?status=failed_permanent`, `POST .../notifications/{id}/retry`, `GET /api/rendering-service/renditions?status=failed_permanent`, `POST .../renditions/{id}/retry`, `GET /api/ocr-service/ocr-results?status=failed_permanent`, `POST .../ocr-results/{id}/retry` (all since **Post-Roadmap Phase 20 Session 7**, [ADR 0083](../adr/0083-admin-ui-processing-failures-visibility.md), `ProcessingFailuresView`) |
| Query console | `GET /api/query-service/query/events?actor=&subject=&event_type=&since=&until=` (since **P8-S1**, `QueryConsoleView`); since **P8-S2b** additionally `GET/POST /api/query-service/manipulation-mode/{status,activate,deactivate}`, `POST /api/query-service/manipulate/{dry-run,execute}`, `GET /api/permission-service/approval-requests?status=pending`, `POST /api/permission-service/approval-requests/{id}/approve` (the latter two already-existing endpoints, reused) |
| Configuration packages | `GET /api/config-service/config/export`, `POST /api/config-service/config/compare`, `POST /api/config-service/config/import` (all since **P17-S1**, `ConfigPackages`) |

## Auth State

`src/lib/auth-context.tsx` — installation-bound since P4-S5 (see above), otherwise like the User UI: `localStorage` tokens, proactive refresh — deliberately duplicated instead of shared (ADR 0006: no shared business logic between independently deployable frontend apps). Since **P6-S5**, additionally loads `permissions: string[]` (`getEffectivePermissions`, Permission Service) right after `user` — native domain admin capabilities (4.6), deliberately separate from `user.realm_roles` (Keycloak), see [ADR 0023](../adr/0023-superuser-breakglass-and-domain-admin-accounts.md).

## Internationalization (Concept 8, since P4-S3)

Like the User UI: `src/i18n/de.json` + `useI18n()` (see [ADR 0007](../adr/0007-frontend-i18n-preparation.md)). Own dictionary, since Admin UI terms (user management, object type editor, registry, installations) are entirely different from the User UI terms.

## Authorization

**Enforced since P6-S5 for a first case** (4.6): `/users/` checks the capability `admin.user_management` — `AdminSidebar` hides the nav entry without this capability, `RequireCapability` (a sibling of `RequireAuth`) additionally protects the route itself (defense in depth against directly called URLs), and the actual backend endpoint (`auth-service` `/users`) enforces it independently of UI state. For **all other** administrative areas (object type editor, registry, installations, OCR/upload/reference-number settings, storage guard), the previous gap still applies: the gateway only checks that a valid bearer token is present, not whether the principal is authorized for the respective administrative action — any successfully logged-in user can currently use them fully. The underlying domain admin roles for this are already seeded in `permission-service` (see `docs/services/permission-service.md`), but without an associated account and without enforcement at the respective endpoint — will follow with the respective domain's future retrofit session. **Exception since P17-S1**: `/config-packages/` is the second case with actual server-side enforcement — `POST /api/config-service/config/import` itself requires `admin.object_config` (see `docs/services/config-service.md`); the Admin UI page only hides the sidebar entry without this capability (`requiresCapability`), but like the majority of the other capability-gated pages (e.g. `/query-console/`), deliberately forgoes an additional `RequireCapability` wrapper around the route itself.

**Since P6-S6 additionally for not-shutdown (4.8)**: `SuperuserBreakGlass` only shows the trigger form if `permissions` (from `auth-context.tsx`) contains the capability `system.not_shutdown.trigger` — purely client-side UX anticipation, actual enforcement happens at the Permission Service (`403` without the capability). The lift button is additionally tied to a second condition that is not purely role-based: it only appears if the currently logged-in principal (`user.sub`) matches `status.principal_id` (the active superuser) — any other person with `system.not_shutdown.trigger` does not see the button, even though they would be allowed to trigger maintenance mode (triggering and lifting are deliberately different permissions, 4.8).

## Build & Delivery

Two-stage Docker image (`apps/admin-ui/Dockerfile`), identical to the User UI. `NEXT_PUBLIC_GATEWAY_BASE_URL` as a build arg (initial value of the "Local" installation), overridable via `ADMIN_UI_GATEWAY_BASE_URL` in `infra/.env`. Port `3001` (User UI: `3000`). Additional installations are added at runtime via `/installations/`, not via a rebuild.

## Tests

- `npm run typecheck` / `npm run lint` / `npm run build`.
- `npm test` (Vitest + Testing Library, **204 tests** — since **Post-Roadmap Phase 22 Session 7** (see
  "Storage Guard" above): `storage-guard.test.tsx` extended by three tests (toggling the
  object-lock mode including reload, toggling the records-disposal role, error display on
  `422`); previously 201 — since **Post-Roadmap Phase 22 Session 6** (see
  "Storage Service Operational Parameters"/"Signature Connectors" above): new test file
  `storage-operational-config.test.tsx` (4 tests: load/display, unreachable state, saving
  changed values, error display on `422`) for `StorageOperationalConfig`, new test file
  `signature-config.test.tsx` (6 tests: listing including level checkboxes, empty state,
  unreachable state, toggling including reload, no call when attempting to deselect the last
  level, error display on `422`) for `SignatureConfig`; previously 191 — since **Post-Roadmap Phase 22 Session 5** (see
  "Teamspaces Admin Overview" above): new test file `teamspaces-admin.test.tsx` (4 tests: listing
  including teamspaces the caller is not themself a member of, empty state, unreachable state,
  error display on missing capability) for the new `TeamspacesAdmin`, plus two new
  `admin-sidebar.test.tsx` tests for the capability gating of the new sidebar entry
  "Team Workspaces"; previously 185 — since **Post-Roadmap Phase 22 Session 3** (see
  "Four-Eyes Settings" above): new test file `approval-settings.test.tsx` (6 tests: empty state,
  unreachable state, sorted listing including `required_permission`/status, toggling including
  retention of `required_permission`, creating a new action type, error display on toggle failure) for the new
  `ApprovalSettings`; previously 179 — since **Post-Roadmap Phase 22 Session 2** (see
  "Group Management" above): four new tests in `user-management.test.tsx` (empty state without groups,
  creating including reload, listing + deleting, expanding including loading the member list/adding/removing
  a member); previously 175 — since **Post-Roadmap Phase 22 Session 1**
  (see "Records Disposal & Long-Term Archiving" below): two new tests in `archival-transfers.test.tsx`
  for the new "dispose now" form (success case including hint text, error display on
  `ApiError`); previously 173 — since **Post-Roadmap Phase 20 Session 7**
  ([ADR 0083](../adr/0083-admin-ui-processing-failures-visibility.md)) nine new tests: three in
  `archival-transfers.test.tsx` (no restart button for just `failed`, restart for `failed_permanent`
  for both the document AND case sections including reload), new test file `processing-failures.test.tsx` (6 tests:
  loading all three sections with the `status=failed_permanent` filter, empty states per section,
  unreachable state, one restart test case per section) for the new `ProcessingFailuresView` against
  `notification-service`/`rendering-service`/`ocr-service`; previously 164 — since **P17-S3** three new tests: two in
  `user-management.test.tsx` (creating a role assignment including reload on `status="created"`,
  four-eyes hint without reload on `status="pending_approval"`, `permission.role_assignment.create`)
  and one in `config-packages.test.tsx` (four-eyes hint without a results table on
  `status="pending_approval"` for `config.import`) — both components have consumed a
  status envelope instead of the previous flat object since P17-S3, see `docs/services/permission-service.md`/
  `config-service.md`; previously 161 — since **P17-S1** new test file `config-packages.test.tsx` (7 tests: hint text, loading a package file including manifest/category display, error display on invalid JSON, hint on missing manifest, preview via `compareConfig` including delta display, applying via `importConfig` including results table, exporting the current configuration as a download) for the new `ConfigPackages` component — file upload via `userEvent.upload` + `FileReader` (see the component comment on `File.text()` in jsdom); since **P14-S11** new test file `delegations-admin.test.tsx` (5 tests: empty state, unreachable state, listing including status badges and exactly one revoke button for the active row, revoking after confirmation, no revocation on a declined confirmation) for the new `DelegationsAdmin` against the Permission Service; previously 147, before that 135 — since **P14-S10** new test file `share-link-settings.test.tsx` (3 tests: loading/displaying the configuration, empty state for an unreachable `document-service`, saving changed values) for the new `ShareLinkSettings` against the Document Service; previously 135, before that 127 — since **P8-S2b** `query-console-view.test.tsx` extended with the manipulation section (8 new tests: visibility behind `admin.query_console.manipulate`, activating/deactivating the safety switch, dry-run→execute flow for a non-critical action, critical badge + pending-approval result for a critical action, JSON parse error in the `object_type.update` value field, rendering the approval table + approve call, empty state); previously 127, before that 120 — since **P8-S1** new test file `query-console-view.test.tsx` (5 tests: hint before the first query, query including rendering the result rows and the "N of M visible" filter hint, empty state with the filter hint hidden, superuser hint instead of the filter hint, error display) for the new `QueryConsoleView` against the new `query-service`, plus two new `admin-sidebar.test.tsx` tests for the capability gating of the new "Query Console" sidebar entry; previously 120, before that 116 — since **P7-S3b** `archival-transfers.test.tsx` extended with `CaseArchivalSection` (4 new tests: configuration + empty state, status table including the download button conditional on `released`, package download including a blob mock, saving the configuration); previously 116, before that 107 — since **P7-S3** a new test file `archival-transfers.test.tsx` (6 tests: list including status/format display, empty state, unreachable state, no retrieve button for `pending`, successful retrieval after confirmation including reload, no retrieval on a declined confirmation) for the new `ArchivalTransfersView` against the new `archival-service`, plus three new `ObjectTypeEditor` tests for the records-disposal fields (`default_archive_after_days`/`archive_encryption_enabled` visible for both `applies_to` values, submitted on create, loaded from existing values when editing); previously 107, before that 96 — since **P7-S2c** two new test files `forensic-trace-view.test.tsx` (6 tests: hint before the first query, query with `queried_by`=current username and rendering categorized matches, empty state, anomaly banner, error display, CSV export) and `audit-trace-settings.test.tsx` (5 tests: loading/displaying the base configuration with default "both on", empty state without overrides, saving changed base values, displaying/deleting an override — deliberately using `within(row)` here instead of a page-wide `getByText`, since "on"/"default" occur both in the override table and in the tri-state selects of the creation form —, creating a new override); previously 96, before that 87 — since **P7-S2b** new test file `reports-view.test.tsx` (9 new tests: empty states for all four reports + the schedule list, rendering real rows per report type, error display on a failed load, CSV export call including `triggerBrowserDownload`, creating/deleting a schedule) for the new `ReportsView` against the new `reporting-service`; previously 87, before that 86 — since **P7-S1b** `RetentionSettings` split into two independent sections (documents/folders, each with its own load/save/error state) and `DeletionRegister` merges the document and folder deletion registers on the display side; previously 86 tests, before that 77, 9 new — 3 new `ObjectTypeEditor` tests for the retention fields plus two new test files `RetentionSettings`/`DeletionRegister`, see P7-S1 additions below): API client (including routing via the currently set gateway address), `AuthProvider` (login/logout/session restoration, since P4-S5 additionally: session isolation between two installations, no re-login when switching back, since P6-S5 additionally: `permissions` is loaded and exposed after login/session restore), `InstallationProvider` (bootstrap, add/switch/remove, protection against removing the last installation, persistence), `ThemeProvider` (since P4-S6: default `auto`, `data-theme` attribute, `localStorage` cache restoration, `setTheme` persistence), `AdminSidebar` (expanding/collapsing groups including persistence, since P6-S5 additionally: the `/users/` entry is hidden without the `admin.user_management` capability), `InstallationManager`/`InstallationSwitcher`, `UserManagement`, `RegistryOverview`, since P6-S5 additionally `SuperuserBreakGlass` (status active/inactive including expiry timestamp, empty state for an unreachable `auth-service`, requesting activation and approving each as the currently logged-in principal), since **P6-S6** additionally in `SuperuserBreakGlass` (not-shutdown form hidden without the capability `system.not_shutdown.trigger`, triggering as the currently logged-in principal, lift button visible only for the active superuser themself) as well as a new `MaintenanceBanner` (no rendering when maintenance mode is inactive, display when maintenance mode is active, stays silent when the Permission Service is unreachable), since P5b-S3 additionally `ObjectTypeEditor` (structured attribute capture, label-driven initial layout persistence, edit mode including retention of `naming_constraints`/`conditions`, icon field only for folder classes, since P5e-S3 additionally the reference-number format/display-override field only for document classes including loading existing values) and `LayoutDesigner` (generated vs. saved layout, row/field operations, save/reset), since P5b-S5 additionally `OcrSettings` (loads/shows the current configuration, saving changed values, empty state for an unreachable `ocr-service`, since P5d-S1 additionally the content-type allowlist), since P5d-S1 additionally `UploadSettings` (loads/shows the format allowlist, saving changed values, empty state for an unreachable `document-service`), since P5b-S6 additionally `StorageGuard` (status table including re-replication badge, saving the admin override toggle, empty state for an unreachable `storage-service`), since P5c-S2 additionally two `StorageGuard` tests for the "accept storage device swap" button (confirmation accepted → reload shows the new device ID, confirmation declined → no API call), since P5e-S3 additionally `KennzeichenSettings` (loads/shows the global default, saving, empty state for an unreachable `object-type-service`), since **P7-S1** additionally `ObjectTypeEditor` (retention-period/deletion-reason-required fields visible for both `applies_to` values, including loading existing values), `RetentionSettings` (loads/shows both configs, saving, empty state for an unreachable `document-service`) and `DeletionRegister` (empty state, rows with resolved trigger labels, empty state for an unreachable `document-service`) — network layer mocked, same rationale as with the User UI. `matchMedia` is polyfilled in `tests/setup.ts`, since jsdom does not implement it.
- **No browser available in this development environment** (see `docs/services/user-ui.md`) — every Admin UI gateway call was individually traced via `curl` against the real running Compose stack (since P4-S6 additionally: `GET/PUT /me/preferences` including 422 for an invalid theme value; since P5b-S3 additionally: the triple `PUT .../layouts/{purpose}` sequence sent by `ObjectTypeEditor` on creation with differing display names, as well as a `PUT /object-types/{id}` with `naming_constraints`/`conditions` passed through unchanged, both replicated 1:1; since P5b-S5 additionally `GET/PUT /api/ocr-service/config`; since P5b-S6 additionally `GET/PUT /api/storage-service/guard-config` and `GET /api/storage-service/guard-status` against a real simulated storage device swap; since P5c-S2 additionally `POST /api/storage-service/guard-status/{target_id}/reidentify` against a real storage device swap accepted without a restart; since P5e-S3 additionally `GET/PUT /api/object-type-service/kennzeichen-config` as well as a complete end-to-end run through the gateway with a real login: create an object type with `kennzeichen_format`, create a document with a spoofed client-side `Kennzeichen` (server value wins), `PATCH` without the `dms-admin` role → `403`; since **Post-Roadmap Phase 20 Session 7** additionally verified directly against the newly `document_id`-optional `GET /api/rendering-service/renditions?status=failed_permanent`/`GET /api/ocr-service/ocr-results?status=failed_permanent` — both returned real `failed_permanent` records across multiple documents, stemming from earlier live verifications of this phase, confirming the cross-document filtering with real rather than synthetic data; `GET /api/notification-service/notifications?status=failed_permanent` correctly returned an empty list; the new `/processing-failures/` route is served by the `admin-ui` container). The multi-installation behavior, theme switching, and the new guided forms/the layout designer/the OCR settings page/the storage guard status block/the reference-number fields/the new `ProcessingFailuresView` itself (row/field interactions, badges, conditional fields) are purely client-side and were **verified only via the Vitest component tests, not visually in a browser** — explicitly communicated to the user as a limitation.

## Open Points

- **Authorization enforced only for `/users/`** (see above, since P6-S5) — all other administrative areas remain ungated, still an important open point; the new `/retention-settings/`/`/deletion-register/` pages (P7-S1) and `/archival-transfers/` (P7-S3) are no exception — the latter always shows the retrieval button regardless of role, the server-side `403` from `archival-service` is the actual enforcement.
- ~~No "dispose now" control (5.6, since P7-S3) — `ArchivalTransfersView` is pure observation/retrieval; a manual trigger would have to go through `document-service`'s `POST /documents/{id}/archive-request`, for which there is no Admin UI integration yet~~ — **fixed in Post-Roadmap Phase 22 Session 1** (see "Records Disposal & Long-Term Archiving" above): a new form calls the endpoint by document ID. Still **no** button directly on the document in the User UI (the Admin UI has no document list/search, only free-text ID entry) — a possible future extension.
- **Not-shutdown control (4.8, since P6-S6) is purely client-side for visibility, not enforcement** — `SuperuserBreakGlass` only hides the form/button, actual enforcement happens exclusively at the Permission Service; no new nav entry, since it is content-wise coupled to the existing break-glass page (4.8 itself references 4.6).
- `naming_constraints`/`conditions` still have no guided UI form (only retained unchanged when editing) — free-text/JSON editing of these two fields is not part of P5b-S3 and remains an open point for a later session.
- No retroactive-impact check when an attribute is renamed/removed during editing while it is already referenced in a saved layout override (same limitation as ADR 0014) — the object type editor deliberately does not touch existing layouts, the next time the affected layout is opened in the layout designer would show the orphaned reference.
- Layout designer does not support multi-column rows via drag and drop, only the unambiguous row/field operations described above (deliberate design decision without browser verification, see above).
- Icon selection is a curated, frontend-hardcoded set of seven icons (no upload) — Concept 13's open point on icon format/origin is thus pragmatically, but not conclusively, answered for this session; switching to a larger or customizable icon set would remain backward-compatible, since the backend only stores a free string.
- No group management, only individual users (Permission Service already supports `principal_type=group`, the UI only offers `user`).
- Workflow designer, license overview, audit trail view, configuration import/export (Concept 8 names these for the Admin UI) are not part of this base scaffold — the underlying services do not yet exist.
- i18n only structurally prepared (ADR 0007), no second language and no UI language switch.
- Installation list is stored purely locally in the browser, no cross-device provisioning (see ADR 0008 "Consequences") — that would be the task of the optional, not-yet-built Fleet/License Management Service (Concept 3a, Phase 13).
- Theme preference has no conflict resolution mechanism between devices/installations (last fetch wins) and no retry on a failed `PUT /me/preferences` (see ADR 0009 "Consequences").
- `ocrEnabled` is not editable on the new OCR settings page, only indirectly visible (reachable/unreachable) — actual on/off switching remains a deployment action (Compose profile), see ADR 0016.
- The Storage Service's target set (which backends/credentials are configured) is likewise not editable on the storage guard page, only the admin override — target-set changes remain deployment configuration (`DMS_TARGETS`), see ADR 0017.
