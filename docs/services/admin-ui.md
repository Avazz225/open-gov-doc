# admin-ui

**Responsibility:** Administrative web interface — user/role management, object type editor, registry/service overview, managing multiple installations from a single Admin UI (Concept 8).
**Concept Reference:** 8, 3a, 3.10
**No own Postgres schema** — a pure client-rendered SPA (static export, see [ADR 0006](../adr/0006-user-ui-static-export-spa.md)), no own backend process.

## Location in the Repo

`apps/admin-ui/` — identical pattern to `apps/user-ui` (not under `services/`, see ADR 0006): Next.js/TypeScript, `output: "export"`, served via `nginx`, no Node process at runtime.

## Login illustration (Phase 83 Session 3, [ADR 0237](../adr/0237-stageshow-adoption-illustrations-and-craft-lens.md))

At viewport widths of 1200px and above, `/login/` shows a 720×400 faux-UI illustration to the left of
the login card, with its pane's top edge level with the card's. It shows a still of the object-type editor for the document class *Eingangsrechnung*: its Kennzeichen format `{YYYY}-{Laufende_Nummer}`, with one leader to its effect, the document row `2026-114 Rechnung_Mai.pdf` as user-ui shows it.

Built with the vendored Stageshow kit in
[`libs/dms-showcase/admin-ui-login`](../../libs/dms-showcase/README.md); rehearsal notes are in its `REHEARSAL.md`.

The host, `src/components/LoginIllustration.tsx`, is a copy of user-ui's (ADR 0006), with the same rules:
- Below 1200px and in the high-contrast theme it renders nothing, and the module is never loaded.
- It follows `<html data-theme>` at runtime.
- Under reduced motion it shows the final frame.
- It has one DE/EN text alternative (`login.illustration.alt`), with its content `aria-hidden`.

Labels the real UI already has come from the app's own i18n keys (`objectTypes.pageTitle`, `objectTypes.kennzeichenFormatLabel`). Only the sample data and the
text alternative live under `login.illustration.*`.

The build wiring is the same four additions as user-ui: `lucide-react` `0.468.0`, `resolve.modules`,
tsconfig `paths` and the Dockerfile `COPY`. `tests/login-illustration.test.tsx` covers the gating.

## Home launcher, registry labels, short ids (Phase 83 Session 7)

**Home launcher.** The home page's area cards (`DashboardWidgets`) were unstyled, underlined, bulleted
link lists that repeated the sidebar. They are now cards in the shared material with tappable rows
(≥ 40px, hover fill, a chevron for "leads on").

The launcher is *kept*, deliberately. Narrow-width support is a goal (ADR 0237 Decision 6), the sidebar
will collapse on small screens, and this launcher then becomes the way in.

**Other fixes:**
- Registry status labels are translated ("Erreichbar"/"Nicht erreichbar"; they were English in both
  languages).
- The archival table's yes/no cells use `common.yes`/`no` instead of ✓/—.
- The reports' folder ids show short and monospaced, with the full id on hover.
- The query console's per-row "Genehmigen" is secondary.
- Status pills (`.badge`) never wrap.

## Narrow widths (Phase 83 Session 12, [ADR 0238](../adr/0238-narrow-width-patterns-for-all-apps.md))

Before, the sidebar kept its width at 390px and left the content about 145px, so every page was 509–983px
wide. Wide tables overflowed at 768px too. Now no page of all 32 routes overflows at 390, 768 or 1024px.

- **Drawer below 768px** (`components/NavDrawer.tsx`). A menu button left of the title opens the
  sidebar (`AdminSidebar variant="drawer"`, rows ≥ 40px) as a modal dialog:
  - focus moves in and is trapped, and Escape and the backdrop close it;
  - the page behind is scroll-locked, and focus returns to the menu button;
  - it closes when a link navigates, and when the window grows past 768px.

  From 768px the sidebar column is unchanged. The home launcher (P83-S7) stays the landing page's way
  in.
- **Account menu** (the same file as in the other apps; at every width since P86-S1, see below). The
  installation switcher comes first through its `leading` slot. All three switchers got the `touch`
  form. The title truncates in one line (`text-xl` narrow) instead of the header wrapping.
- **All 38 `.data-table`s** sit in a `dms-scroll-x` wrapper. Inside a `bg-surface` card the scroll covers
  take the surface color automatically (`layout.css`).
- **`.form-grid`** goes to one column below 640px. Main padding drops to 16px.

At 1024 and 1400px the content area is pixel-identical before/after. `e2e/narrow.spec.ts` covers no
overflow on the wide-table pages, drawer open/close/focus and navigation, and the account menu.
`tests/nav-drawer.test.tsx` covers the dialog behavior.

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
| `/export-settings/` | PDF export behavior + output stamping of the Document Service (`ExportSettings`, 14.x, since **Post-Roadmap Phase 36 Session 1**, [ADR 0107](../adr/0107-pdf-export-two-pass-merge-subnumbering.md)/[ADR 0117](../adr/0117-output-stamping-qr-barcode-position-export-pipeline.md)) — same load/save pattern as `OcrSettings`, against the Document Service; API-only since both phases, see below |
| `/storage-guard/` | Storage-device-swap guard status + admin override of the Storage Service (`StorageGuard`, since P5b-S6) |
| `/storage-operational-config/` | Write strategy/quorum/max replication attempts of the Storage Service (`StorageOperationalConfig`, 3.6, since **Post-Roadmap Phase 22 Session 6**, [ADR 0091](../adr/0091-connector-operational-config-live-editable.md)) |
| `/kennzeichen-settings/` | Global default "show reference number before filename" of the Object-Type Service (`KennzeichenSettings`, since P5e-S3) |
| `/retention-settings/` | Installation-wide retention/trash configuration (`RetentionSettings`, 5.2/5.2a, since P7-S1) — two independent sections since P7-S1b: Document Service and Folder Service (own, independently configurable settings) |
| `/deletion-register/` | Read the deletion register (`DeletionRegister`, pure read-only table, 5.2a, since P7-S1) — since P7-S1b shows documents **and** folders together in one table (column "Type"), since **P89-S2** also destroyed case binders (Vorgangsnummer above the id, mode as trigger, removed reference count); an unreachable case-service only hides the cases, with a note. `manual_purge` got its own label (it showed as trash expiry before) |
| `/archival-transfers/` | Records-disposal transfer status + retrieval (`ArchivalTransfersView`, 5.6, since P7-S3, since **P7-S3b** additionally a second section for cases, since **Post-Roadmap Phase 20 Session 7** additionally a `failed_permanent` filter + restart button in both sections, since **P89-S2** the case destruction schedule in the case settings form and a third section "Vernichtung von Umlaufmappen" with the destruction runs: Vorgangsnummer, status badge with the blocking reason, mode, time, retry for `failed_permanent`) — pure status table(s) against `archival-service`/`case-service`, see below |
| `/processing-failures/` | "Permanently failed" visibility + manual restart (`ProcessingFailuresView`, since **Post-Roadmap Phase 20 Session 7**, [ADR 0083](../adr/0083-admin-ui-processing-failures-visibility.md)) — four sections (notifications/renditions/OCR results against `notification-service`/`rendering-service`/`ocr-service`; since **Phase 40 Session 3** a fourth against `federation-hub-service`'s handovers, reached DIRECTLY rather than through the gateway), see below |
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
- **`is_org_unit` (Post-Roadmap Phase 35 Session 1, [ADR 0143](../adr/0143-org-unit-marker-and-org-hierarchy-grant-admin-visibility.md))**:
  the creation form gained a checkbox (with an inline explanation of what it's for — spans the full
  `.form-grid` width via a new `.form-grid > .hint { grid-column: 1 / -1; }` CSS rule, matching the
  existing `.deletion-reason-catalog` full-width precedent), and the group table gained a new
  "Organisationseinheit" column whose badge-styled button doubles as the toggle (`PATCH /groups/{id}`) —
  the first editable field a `Group` row has ever had in this UI.

## Org-Hierarchy Management (14.2, Post-Roadmap Phase 31 Session 9, [ADR 0120](../adr/0120-org-hierarchy-supervisor-dag-groups-as-org-units.md))

New "Organisations-Hierarchie" section in `UserManagement.tsx` (`/users/`), right after Groups — same
page, same audience, since org-hierarchy data is administered exactly like groups/roles
(`admin.user_management`). Uses `permission-service`'s new `SupervisorAssignment` edges (see
`docs/services/permission-service.md` "Org-Hierarchy Foundation").

- Form for creating an assignment (principal ID + supervisor principal ID, both free-text — same
  deliberate simplicity as group membership's `principalId` field, no picker), flat table of all
  assignments with a "delete" button per row — no expand/collapse needed here, unlike Groups, since a
  `SupervisorAssignment` row has no nested sub-list of its own.
- A second, small form below the table: a chain-lookup tool (enter a principal ID, see the resolved
  transitive supervisor chain via `GET /supervisor-chain/{id}`) — this is the actual value the session
  exists to demonstrate/verify (the plan's own wording: "a principal's direct supervisor, and the
  supervisor chain derived from it"), and gives immediate admin value even before P31-S10/S11 have a
  feature that consumes the data.
- **No new RBAC special case in the frontend**, same reasoning as Groups above — page-level gating plus
  the server-side `403` is the actual enforcement.

## Four-Eyes Settings (Post-Roadmap Phase 22 Session 3, [ADR 0089](../adr/0089-approval-settings-ui-config-endpoint-stays-ungated.md))

**Since P90-S3** ([ADR 0247](../adr/0247-approver-notification-and-role-based-recipients.md)):
- **"Benachrichtigt" column:** a role choice per action type ("Standard" = holders of the required
  permission, else the "Freigabe-Benachrichtigung" role), at most 14rem wide.
- **"Wer?":** opens a row with the resolution source and the names (`PrincipalName`), refreshed after a
  change.
- **The toggle** sends only `requires_approval`, since the backend now keeps fields that aren't sent.

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

## Role-Dependent Dashboard + Installation Branding (Concept 8, P69-S2, [ADR 0202](../adr/0202-p69s2-branding-config-and-role-dependent-dashboard.md))

- **`DashboardWidgets`** (home page, `/`): the previously bare `home.hint` paragraph now also renders a widget grid — one `.card` per `AdminSidebar` nav group that has at least one item visible to the current principal, reusing `AdminSidebar`'s own exported `GROUPS`/`visibleItems` (same `requiresCapability` filter, not a second authorization mechanism). A domain admin who only holds e.g. `admin.storage` sees only the "Storage" widget; a superuser sees the full grid. Live-verified in both themes with the `users-admin` technical account, confirming the sidebar and the dashboard agree on which groups are visible.
- **`BrandingProvider`** (`lib/branding-context.tsx`, nested inside `InstallationProvider`/`ThemeProvider` in `layout.tsx`): fetches `registry-service`'s `GET /installation/branding` once per active installation (re-fetches on `switchInstallation`), applies `product_name` to `document.title` and the login heading/home title (`AdminShell`'s `title` prop), and `accent_color` as a `--dms-accent`/`--dms-accent-bg`/`--dms-accent-bg-strong` inline override on top of the static `libs/dms-ui` tokens — skipped entirely in high-contrast mode (that theme's accent values are a deliberate, legibility-driven fixed choice, not a brand color to override). All three fields default to `null` (this build's static values), so an installation that never sets branding sees no change at all.
- Same implementation, deliberately duplicated per app (ADR 0006), in `user-ui`/`reviewer-ui`/`process-designer`/`migration-console`; `office-addin` deliberately excluded (see `docs/services/office-addin.md`), `libreoffice-addin` out of scope (no browser DOM).

## Guided Object Type Editor + Form Layout Designer (2.2/2.2a/2.2b, since P5b-S3)

Replaces the free-text JSON attribute editor from P4-S3 with two separate areas on the same page:

- **`ObjectTypeEditor`**: since **P6-S7** additionally a conditional "Minimum signature level" dropdown (none/SES/AES/QES, visible only for `applies_to="document"`, `required_signature_level`, 3.10) — pure configuration, actual enforcement happens during signing in the Signature Service, see `docs/services/signature-service.md`. A structured attribute builder instead of a JSON textarea (one row per attribute: technical name, type, required flag, a "personal data" checkbox since **Post-Roadmap Phase 41 Session 2** ([ADR 0156](../adr/0156-attribute-pseudonymization-reversible-vault.md), 5.2 — marks the attribute eligible for `document-service`'s attribute pseudonymization vault), plus pattern/minimum/maximum depending on type), checkboxes for `allowedParentTypes` (2.2a: `"$ROOT"` + all existing folder classes except the one currently being edited) and an `icon` selection field (2.2a/2.2b, visible only for `applies_to="folder"`) from a **curated, hardcoded icon set** (`folder`, `folder-open`, `folder-star`, `archive`, `briefcase`, `invoice`, `contract` — a deliberate decision against free SVG upload, see Concept 13 "open points": a curated set fully avoids the active-content security concern for uploaded SVGs named there, at the cost of design freedom). Now also supports **editing** existing object types (`PUT /object-types/{id}`) — `name`/`applies_to` remain server-side immutable and are locked in the form; `naming_constraints`/`conditions` are passed through unchanged on save (no UI for them in this session), so that saving via the new editor does not silently reset them to their default.
- **Display names are a creation-time feature, not a permanently editable attribute property**: labels live in the form layout, not in the attribute schema itself (see [ADR 0014](../adr/0014-form-layout-generated-defaults-not-persisted.md)). Only when **newly creating** an object type does the attribute builder show an additional "display name" field per attribute; if at least one display name differs from the technical name, an initial smart layout (2 attributes per row, like `object_type_service.layout.generate_smart_layout`) with these labels is saved right after creation for all three usage purposes (display/search/upload) (`PUT .../layouts/{purpose}` three times). Without differing labels, the server-generated default remains in place — no unnecessary override. When **editing** an existing object type, the display-name field is deliberately absent — later label adjustments run exclusively via the layout designer, so the object type editor does not overwrite targeted layout adjustments already made there.
- **`LayoutDesigner`** (own area below the object type editor): object type and usage-purpose selection (display/search/upload), shows the current layout including a badge ("automatically generated" vs. "customized", from the API's `is_custom`). Editing exclusively via unambiguous row operations instead of free dragging of individual fields between rows (move row up/down, remove row, create new empty row, move a field within a row left/right, remove a field from the row, add an attribute from the not-yet-used attributes into a specific row) — moving a field between two rows therefore takes two steps (remove, then add in the target row) instead of a single gesture; deliberately decided this way, since this environment has no browser for visual verification of a drag-and-drop interaction (see "Tests" below) and every row operation remains unambiguous and testable with Vitest this way. "Save" (`PUT`) and "reset to generated layout" (`DELETE`, then reload) independently per object type and usage purpose. **Since Post-Roadmap Phase 33 Session 1** ([ADR 0135](../adr/0135-accessibility-audit-five-frontend-apps.md)): the within-row `◀`/`▶` move buttons gained real `aria-label`s (`layoutDesigner.moveFieldLeft`/`moveFieldRight`) — previously only the raw glyph, unlike their sibling row-level "move up"/"move down" buttons, which already had full text labels.
- **File-reference-number generator fields** (2.2, since P5e-S3): visible only for `applies_to="document"` — a format-string free-text field (`kennzeichen_format`, placeholder hint as `hint` text) and a tri-state selector for `kennzeichen_display_override` ("use global default"/"always show"/"never show"). Forced to `null` client-side as soon as `applies_to="folder"` is chosen — mirrors the same zero-trust stance as the `icon` field (server-side 422 validation remains the actual safeguard, the frontend only avoids the unnecessary error case).
- **Retention fields** (5.2/5.2a, since P7-S1): unlike the reference-number/signature fields, **visible for both `applies_to` values** (the object type schema applies equally to documents and — since P7-S1b — folders) — a number field "default retention period in days" (`default_retention_days`, empty = no default) and a tri-state selector for `deletion_reason_required_override` ("use global default"/"always required"/"never required").
- **Classified-document classification** (2.5, since P15-S1, multi-level since P17-S2/14.2): visible only for `applies_to="document"` — a `<select>` with the four common German VS classification levels (`VS-NfD`/`VS-VERTRAULICH`/`GEHEIM`/`STRENG GEHEIM`) plus "unclassified". **Until P17-S1 a plain checkbox** (`is_classified: bool`) — replaced, not extended, since the backend field itself became `classification_level: str | null`, see [ADR 0059](../adr/0059-egov-paket-aktenplan-hierarchie-und-mehrstufige-vs-einstufung.md).

## Reference-Number Settings (2.2/8, since P5e-S3)

`KennzeichenSettings` (`/kennzeichen-settings/`) edits the same kind of configuration as `OcrSettings`/`UploadSettings`/`StorageGuard`, but this time against the **Object-Type Service** (`GET`/`PUT /api/object-type-service/kennzeichen-config`): a single checkbox toggle "show reference number before filename" as a global default — deliberately not a catalog of several independent display points (tab title, list prefix individually toggleable), see `PROGRESS.md` "File-Reference-Number Generator" for the rationale of this simplification. Individual document types override this default via the tri-state field in the object type editor. Same "unreachable" empty-state pattern as the other configuration pages for a connection error.

## OCR Settings (3.9, since P5b-S5, [ADR 0016](../adr/0016-ocr-configurability-compose-profile-and-live-settings.md))

`OcrSettings` (`/ocr-settings/`) is the first Admin UI page in this project that edits a **backend runtime configuration** rather than a business object (user, object type, ...) — loads `GET /api/ocr-service/config` and saves changes to the maximum word ceiling (empty = no ceiling), the processing batch size, and (since P5d-S1) a comma-separated content-type allowlist via `PUT` (empty = no restriction, non-empty = only listed content types trigger OCR). Deliberately **no** `ocrEnabled` toggle on this page: whether OCR runs at all is a Docker Compose profile opt-out (the container simply isn't deployed) — an Admin UI cannot start a non-running service at the push of a button. If loading fails with a connection error (no HTTP status code, `ApiError` is not thrown), the page instead shows an explanatory empty state ("unreachable, presumably disabled via a Compose profile") — this is the only visibility of `ocrEnabled=false` in this UI.

## Format Allowlist (3.1/3.6, since P5d-S1)

`UploadSettings` (`/upload-settings/`) edits the same kind of backend runtime configuration as `OcrSettings`, this time for the Document Service: `GET`/`PUT /api/document-service/upload-config`, a comma-separated list of allowed content types (empty = no restriction). The hint text makes explicit that the checked content type is determined from the actual file bytes, not from the header sent by the browser — the allowlist thus correctly applies even when a client sends a wrong/generic content type.

## Export Settings (14.x, since **Post-Roadmap Phase 36 Session 1**, [ADR 0107](../adr/0107-pdf-export-two-pass-merge-subnumbering.md)/[ADR 0117](../adr/0117-output-stamping-qr-barcode-position-export-pipeline.md))

`ExportSettings` (`/export-settings/`) — same load/save/empty-state shape as `OcrSettings`, against the Document Service (`GET`/`PUT /api/document-service/export-config`). Closes a gap that had stood API-only since both underlying phases: `history_position` (`before`/`after` the document content, ADR 0107) and, since Post-Roadmap Phase 31 Session 6, the four `stamp_*` fields (`stamp_enabled`, `stamp_type` of `text`/`qr`/`barcode`, `stamp_value_template` — a `str.format()` template accepting only `{document_id}`/`{kennzeichen}`, `stamp_position` of `diagonal-center`/one of the four corners, ADR 0117). Both new field groups were already fully wired into the live export pipeline (`POST /documents/{id}/export`, `POST /folders/{id}/export`) before this session — this page is purely the missing configuration surface, no backend change. Deliberately **no client-side cross-field validation**: `stamp_position="diagonal-center"` combined with a non-`text` `stamp_type`, and an unknown `stamp_value_template` placeholder, are both rejected server-side with `422` (`document-service`'s own `update_export_config` docstring explains why: checked once at config-write time rather than re-checked on every export) — the page surfaces that message directly via the existing generic error-text pattern, same as every other settings page in this app. Deliberately **ungated** (no `requiresCapability` on the sidebar entry) — matches the backend, which checks no `X-DMS-Principal`/capability on either endpoint, the same posture as `retention-config`/`upload-config`.

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

- **`ProcessingFailuresView`** (`/processing-failures/`) — closes the gap created in ADR 0079/0080: `notification-service`/`rendering-service`/`ocr-service` got retry/backoff + `failed_permanent` there, but no Admin UI integration. Independent sections (no shared generic hook, same "lightweight duplication instead of abstraction" principle as the poll loops of the associated backend services), each loading exclusively the failed-status records of the respective service (`GET .../notifications?status=failed_permanent`, `GET .../renditions?status=failed_permanent`, `GET .../ocr-results?status=failed_permanent`) and offering a "retry" button per row (`POST .../{id}/retry`, already present server-side since ADR 0079/0080). `rendering-service`/`ocr-service`'s list endpoints previously had `document_id` as a required parameter — made optional for this session (see below), `notification-service`'s `GET /notifications` already had the needed `status` filter.
- **`HandoverFailuresSection` (Phase 40 Session 3)** — a fourth section, against `federation-hub-service`'s handovers. Loads BOTH independent failure legs (`GET .../handovers?status=delivery_failed` and `?status=result_delivery_failed`, ADR 0081 + this session) and merges them into one table with a "failed leg" column, since a single `POST .../handovers/{id}/retry` already dispatches on whichever leg actually failed. **Structural deviation from the other three sections**: calls `federation-hub-service` DIRECTLY (`lib/api.ts`'s `federationHubRequest`, a fixed `NEXT_PUBLIC_FEDERATION_HUB_BASE_URL`), not through the gateway/`request()` — this service deliberately does not register with `registry-service` (not an internal service of any one installation, see `docs/services/federation-hub-service.md`), so the gateway's registry-based proxy cannot reach it at all. No `Authorization` header either (the hub has no admin-JWT model). Required adding `CORSMiddleware` to `federation-hub-service` itself (previously only consumed server-to-server).
- **Triggering records disposal itself** (object type deadline/manual trigger) does **not** happen here, but via the extended `ObjectTypeEditor` (`default_archive_after_days`/`archive_encryption_enabled`, see above) or directly on the document in the User UI — this page is pure observation/retrieval, no "dispose now" control (the automatic poll loop of `archival-service` already covers the normal case).
- **The role gate for retrieval is enforced server-side** (`archival-service`'s `archive_retrieval_role`, default `dms-admin`, from the gateway-injected `X-DMS-Roles` header) — the Admin UI itself does not hide the button based on role, a click without the matching role returns `403`, shown in the page's `error` text.
- **Second section "Case Records Disposal"** (`CaseArchivalSection`, same `/archival-transfers/` slug, since **P7-S3b**) — no new page slug, same multi-section pattern as `RetentionSettings` (documents + folders in one form). Contains two parts: **(a)** a configuration form for `CaseArchivalConfig` (`GET`/`PUT /api/case-service/case-archival-config`) — installation-wide disposal deadline after closure + encryption toggle, no counterpart in `ObjectTypeEditor`, since cases have no own `applies_to` category (see `docs/services/case-service.md`). **(b)** a status table (`GET /api/archival-service/case-archival-transfers?status=...`) with case ID, status (`pending`/`locked`/`packaged`/`verified`/`released`/`failed`), encrypted column, "archived at". A "download package" button appears only for status `released`, downloads the (optionally server-side decrypted) ZIP package via `GET .../case-archival-transfers/{id}/package` as a blob (`triggerBrowserDownload`, same pattern as `ReportsView`/`ForensicTraceView`) — **no** writing back to a live target as with document retrieval, since a case has no own live storage space.
- **`NotificationFailuresSection` self-gated since P63-S3**: unlike the other three sections, `GET
  /notifications` requires `admin.notification_read` (Phase 59 Session 1) — this section now checks
  `permissions.includes("admin.notification_read")` from `useAuth()` and, if absent, shows
  `processingFailures.notificationMissingPermission` instead of calling the API, skipping the call
  entirely rather than surfacing the backend's raw `403` text as before. The other three sections and
  the page's nav entry are unaffected — see the note under "Full alignment" above for why this is
  per-section rather than a page-level `RequireCapability`.

## Standard Reports (5.4a, since P7-S2b)

`ReportsView` (`/reports/`) bundles four fixed report sections — document volume, open workflow tasks, storage usage per backend, user activity — plus a fifth section for managing schedulable report runs, all against the new `reporting-service` (see `docs/services/reporting-service.md`). Each report section follows the same pattern: filter form (where applicable), table, two export buttons (`GET .../export?format=csv|pdf`, blob download via the same `triggerBrowserDownload` mechanism as the document download in the User UI). The scheduling section (`ReportScheduleSection`) creates/deletes `report_schedule` rows (report type, format, frequency, recipient email) — the actual dispatch (poll loop + email with a download link) runs entirely in `reporting-service`, this page is only the management UI for it. Deliberately in the Admin UI rather than the User UI — system evaluation/user activity is an administrative matter, the same role split as e.g. `MaintenanceBanner`. **Since P53-S3**: the schedule table gained a "last delivery" status column reading the new `last_status`/`last_error` fields — `null` shows a plain hint ("never run yet"), `"sent"` a `.badge.ok`, `"failed"` a `.badge.down` carrying `last_error` as its `title` tooltip (the same badge convention as `InstallationManager`/`LicenseStatusView`) — surfacing a previously silent poll-tick failure (see `docs/services/reporting-service.md` "Poll Loop") directly in this page instead of only in service logs.

## Forensic Trace & Audit Depth (5.4b, since P7-S2c)

- **`ForensicTraceView`** (`/forensic-trace/`) — filter form (actor, object ID, category dropdown `view`/`download`/`change`/`delete`, time window from/to), results table (timestamp/event type/category/actor/object/service), anomaly banner (red as soon as `anomalies` in the response is non-empty), two export buttons (same `triggerBrowserDownload` pattern as `ReportsView`). Every query (including export) sends the currently logged-in username (`useAuth().user.username`) as `queried_by` — a required parameter of `reporting-service`, since every trace query is itself audited there again (self-audit, 5.4b concept requirement). Before the first query, the page deliberately shows its own hint instead of an automatic initial load — unlike `ReportsView`'s standard reports, which load immediately when opened, an auto-load here would trigger an unfiltered, potentially very broad trace plus a self-audit event on every page visit. **Since Post-Roadmap Phase 36 Session 3**: `reporting-service` now row-level RBAC-filters the trace (see `docs/services/reporting-service.md` "Row-Level RBAC Filtering"), so the page shows the same `"{visible} of {total} events visible"`/superuser-unfiltered hint `QueryConsoleView` already established for its own query results.
- **`AuditTraceSettings`** (`/audit-trace-settings/`) — two sections against the Document Service: base configuration (two checkboxes `log_viewed`/`log_downloaded`, default both on) and a table of role overrides (role, two tri-state selects default/on/off per category, delete button) with a creation form. Both selects in the creation form deliberately carry their own `aria-label` texts, different from the base checkbox labels (`overrideLogViewedLabel`/`overrideLogDownloadedLabel`) — identical accessible names in two places on the same page would have created an unresolvable ambiguity both for screen reader users and for `getByLabelText`-based tests (discovered while writing the tests, see below).

## Query Console (6.1, since P8-S1/P8-S2b)

`QueryConsoleView` (`/query-console/`) — two sections, both against `query-service` (see `docs/services/query-service.md`):

- **Read access** (`QueryEventsSection`, since P8-S1): filter form (actor, object ID, event type, time window from/to), results table (timestamp/event type/actor/object/service). `query-service` actively filters result rows by the executing person's folder permissions — the page makes this transparent: after every query, a hint shows either `"{visible} of {total} events visible"` or, for the activated superuser, its own "unfiltered" hint (the forensic trace, `reporting-service`, gained the identical filtering + hint pattern in Post-Roadmap Phase 36 Session 3, see above). No `queried_by` parameter needed (unlike the forensic trace) — `X-DMS-Principal` is injected by the gateway from the bearer token. **No free-text SQL input field** — `query-service`'s `POST /query` path remains unused anyway without an installed parser plugin (ADR 0031); only the structured filter API (`GET /query/events`) is wired to the UI.
- **Manipulation** (`ManipulationSection`, since **P8-S2b**) — visible only with the fine-grained capability `admin.query_console.manipulate` (in addition to the already-existing sidebar gate, defense in depth — actual enforcement remains server-side in `query-service`). Three subsections:
  - **Safety switch**: status display (active/until when/by whom) + activation form (minute input, default 15)/deactivate button — direct calls to `query-service`'s three safety-switch endpoints.
  - **Action**: `<select>` with the three known `action_type` strings (hardcoded mirror of `manipulation.py`'s catalog), a dynamic parameter form per selection (`document.attribute_reset`: two text fields; `permission.role_assignment.delete`: one number; `object_type.update`: number + field dropdown + raw JSON text field for `value`, parsed client-side before submission). Two-step flow: "simulate" (dry run) shows preview text + a critical badge and holds the `dry_run_token` in component state — only afterward does "execute" appear; any form change discards the token (forces a fresh dry run before every execution). Result `"executed"` shows a success message, `"pending_approval"` shows a hint text and reloads the approval table.
  - **Pending approvals**: `GET /approval-requests?status=pending` against `permission-service` (already-existing, generic four-eyes client function from P6-S5, here called once without an `action_type` filter and filtered client-side to the three known action types — the API only filters `action_type` as a single string, not a list), per row action type/initiator/timestamp/raw parameters (JSON) + "approve"/"reject" buttons. **Since Post-Roadmap Phase 36 Session 3**: reject is wired up via the new `rejectApprovalRequest` (`POST /approval-requests/{id}/reject`, already generically exposed by `permission-service` since it exists), using the same inline reason-form pattern (`rejectingId`/`rejectReason` state, no `window.prompt`) already established in `reviewer-ui`'s `ApprovalList.tsx`. Previously approve-only in this UI.

## Public Share Link (4.2a, since P14-S10)

`ShareLinkSettings` (`/share-link-settings/`) — a form against `GET`/`PUT /api/document-service/share-link-config`: checkbox "allow share links" (`enabled`) + number field "maximum validity period for new links (days)" (`max_validity_days`), same load/save/empty-state pattern as `OcrSettings`. Disabling also takes immediate effect on already-issued links (see `docs/services/document-service.md`/[ADR 0047](../adr/0047-public-share-link-query-param-token-and-disable-semantics.md)), not just on new creations — this page itself does not intervene for that, it is server-side logic in `document-service`.

## Delegation During Absence (4.4a, since P14-S11)

`DelegationsAdmin` (`/delegations/`) — a pure table overview against `GET /api/permission-service/delegations` (unfiltered, all delegations installation-wide): represented person, delegate, time window, status (active/not yet started/expired/revoked), revoke button only for active rows. Creating is deliberately not an Admin UI feature (self-service, per the concept's wording "a person can... set up" — see `docs/services/user-ui.md`'s `DelegationsPane`); this page only maps the second, concept-envisioned revocation path ("... or be ended by an authorized admin role"), enforced server-side via `delegation_revoke_admin_role` in `permission-service`, see [ADR 0048](../adr/0048-delegation-lives-in-permission-service-no-task-assignee-retrofit.md). Since Post-Roadmap Phase 35 Session 1 ([ADR 0143](../adr/0143-org-unit-marker-and-org-hierarchy-grant-admin-visibility.md)), a new "Herkunft" (origin) column reads the row's `grant_kind` field ("Selbstverwaltet" for `null`, else a label per `"supervisor"`/`"supervisor_chain"`/`"org_unit"`) — closing the gap ADR 0121 itself named ("indistinguishable from a self-service delegation except by inspecting `scope_process_definition_ids`' shape") — plus a client-side filter checkbox ("nur automatisch erzeugte Org-Hierarchie-Freigaben anzeigen") that hides self-service rows without a new backend query parameter.

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
as `config-service`'s own import gate), matched by a `RequireCapability capability="admin.
object_config"` wrapper on the page itself (defense in depth for a direct link, the standard
pattern — a stale claim of "no additional wrapper" here was corrected during **Phase 52
Session 2** while adding the sibling config-compare page below). Details/rationale for the
backend changes (manifest field, new `realm_roles` category, split of `config/import`/
`config/fleet-import` at the gateway) see
[ADR 0058](../adr/0058-konfigurationspakete-manifest-realm-roles-and-gateway-import-route-split.md).

## Cross-Installation Config Compare (7.5, since Phase 52 Session 2)

`ConfigCompare` (`/config-compare/`) — [ADR 0040](../adr/0040-config-compare-field-level-diff-no-cross-installation-fetch.md)'s
own deferred "later UI session" ("CLI and raw API only... a later UI session would need to
build its own view for this"). `POST /config/compare` itself has no notion of "installation A
vs. B" — it only ever diffs two already-in-hand `ConfigDocument` payloads, and ADR 0040
deliberately built no automated cross-installation fetch. This page respects that boundary:

- **Base** is always the active installation's own live export — `compareConfig()` called
  with `base` omitted, exactly like `ConfigPackages.tsx`'s own preview, just against the
  active installation's session/`gatewayBaseUrl` as usual.
- **Compare** is fetched via a one-off login scoped to just this screen: the admin picks
  another installation from `useInstallation()`'s known list (`InstallationManager.tsx`'s own
  infrastructure, reused directly — no new installation-registry mechanism), enters that
  installation's own credentials, and the page calls two new `api.ts` primitives, `loginAt`/
  `exportConfigAt`, which take an explicit `gatewayBaseUrl` parameter instead of this module's
  mutable one (a new `requestAt()` helper underneath both `request()` and these two — `request()`
  itself is now a thin wrapper calling `requestAt(gatewayBaseUrl, ...)`). The resulting token is
  used only for that one export call and is never persisted alongside the active installation's
  own token (`dms.tokens.<id>` in `auth-context.tsx`) or exposed via `useAuth()` — this mirrors
  the CLI flow ADR 0040 itself describes ("two `dms config export` calls, each with its own
  login, plus `dms config compare`"), just without leaving the browser tab.
- **Rendering** reuses `ConfigPackages.tsx`'s `DeltaTable` presentation (per-category card:
  only-in-compare, differing, only-in-base), duplicated rather than imported per this project's
  established frontend convention (ADR 0006). Since **Phase 58 Session 2**: each differing item
  is now a `<details>`/`<summary>` disclosure — expanding it shows a per-field table (`field`,
  `base`, `compare`), the backend's own `{base, compare}` detail per field (already returned
  since P14-S1) is finally rendered instead of just the item's name, closing ADR 0040's own
  self-documented "smaller, still-open follow-up".
- **Ignore-regex (since Phase 58 Session 2)**: one optional text input sends the backend's
  already-existing `ignore_regex` parameter ([`CompareRequest.ignore_regex`](../adr/0040-config-compare-field-level-diff-no-cross-installation-fetch.md),
  P14-S1) as a single GLOBAL pattern (`{"*": value}`) — the backend itself supports a per-category
  map too, but this UI deliberately offers only the global form, the smallest increment closing
  the follow-up without a per-category input nothing asked for.

Sidebar visibility and the page's own `RequireCapability` both gate on `admin.object_config`,
the same capability as `ConfigPackages.tsx` — both are config-service admin functionality, and
`POST /config/compare` itself is ungated server-side either way (same as `GET /config/export`).

## Multiple Installations (Concept 3a/8, since P4-S5)

The Admin UI can manage several fully independent DMS installations, without needing to log in again on every switch — see [ADR 0008](../adr/0008-admin-ui-multi-installation-sessions.md) for the technical rationale. Summary:

- Installation list (`{id, name, gatewayBaseUrl}`) purely client-side in `localStorage`, managed via `InstallationManager` (`/installations/`) and `useInstallation()` (`lib/installation-context.tsx`).
- `InstallationSwitcher` in the header switches the active installation — remains hidden as long as only one installation is configured.
- **Own session per installation**: `auth-context.tsx` stores tokens under `dms.tokens.<installationId>` instead of a single global key. Switching to an installation already logged into once requires no re-login as long as its session is still valid; a new, never-logged-into installation shows the login on switch.
- **No single sign-on across installation boundaries** — deliberate, matches the full isolation from Concept 3a.
- `lib/api.ts`'s gateway address has, since this session, been a mutable module variable (`setGatewayBaseUrl()`) instead of a fixed constant, set synchronously by the `InstallationProvider` on every switch.

**Not to be confused with Fleet Management (below)**: this section's "installation" is which gateway THIS browser's admin session logs into — a completely different concept from `fleet-management-service`'s `ManagedInstallation`, an installation a fleet operator remotely administers with no login of its own.

## Fleet Management (P67-S1, [ADR 0197](../adr/0197-admin-ui-fleet-management-view.md))

`FleetManagementView` (`/fleet-management/`) wires up `fleet-management-service`, reached directly at a
fixed base URL (`lib/config.ts`'s `FLEET_MANAGEMENT_SERVICE_BASE_URL`) rather than through the gateway —
same reasoning as Federation Hub below: the service deliberately doesn't self-register with
`registry-service`. Every endpoint (including the plain list) requires the operator secret
(`Authorization: Bearer <key>`, `fleet-management-service`'s own `_require_operator_key`), kept only in
component state, never persisted, same convention as `ProcessingFailuresView`'s
`HandoverFailuresSection`. Covers: registering a new managed installation (shows the plaintext
`fleet_agent_api_key` exactly once, never re-fetchable afterward), listing, deletion, a live status check
(`GET /installations/status`), and pushing a license token (`POST /installations/{id}/license`). Groups,
update plans and rollouts followed in P88-S1 (see "Fleet Orchestration").


**Operator identity (P84-S4, ADR 0234):** after "Laden" the view also calls `GET /operators/me` and shows
"Angemeldet als <name>", with an "Admin-Operator" badge for admins. The field is labelled
"Fleet-Operator-Token" and accepts the env key (operator `bootstrap`) or a personal minted token.

## Theming (Concept 8, since P4-S6)

`src/lib/theme-context.tsx` (`ThemeProvider`/`useTheme()`) — identical pattern to the User UI (deliberately duplicated instead of shared, ADR 0006), toggleable via the `ThemeSwitcher` in the header. Stored across devices on the user account of the **active installation** (`GET/PUT /api/auth-service/me/preferences`, `accessToken` comes from the installation-bound `AuthProvider`, ADR 0008), see [ADR 0009](../adr/0009-cross-ui-theming-profile-persistence.md). The `localStorage` cache key (`dms.theme`) is deliberately **not** installation-specific — switching installations briefly still shows the last-cached theme choice, until the new installation's own preference has been loaded (see ADR 0009 "Consequences").

**Since Phase 49 Session 3** ([ADR 0168](../adr/0168-shared-design-tokens-and-scales.md)): `globals.css`'s own `--dms-*` color-token declarations (the `:root`/`@media`/`[data-theme]` blocks) removed and replaced by `@import "../../../../libs/dms-ui/tokens.css";` — the shared, de-drifted token source built in Phase 48 Session 1. This app was one of the three (with `user-ui`/`process-designer`) still carrying the unfixed high-contrast `--dms-accent-bg: #ffff00` bug; adopting the shared file fixes it. The `.badge.ok`/`.badge.down` high-contrast border override stayed in `globals.css` (app-specific, unrelated to the accent-bg bug).

**Since Post-Roadmap Phase 75 Session 1** ([ADR 0218](../adr/0218-tailwind-css-v4-tooling-foundation.md)): `tailwindcss`/`@tailwindcss/postcss`/`postcss` added as devDependencies, a new `postcss.config.mjs`, and `libs/dms-ui/tailwind-preset.css` imported into `globals.css` (mapping Tailwind utility classes onto the `--dms-*` tokens above) — tooling only, this app's own UI is unchanged (preflight deliberately omitted for now, see the ADR — this app's login page specifically was the one the user named as "clunky, dated," confirmed visually unchanged by this session's own before/after screenshot); the actual redesign starts at P75-S2.

**Since Post-Roadmap Phase 75 Session 2** ([ADR 0219](../adr/0219-login-page-tailwind-redesign-accent-fg-token-cascade-fix.md)): `/login` rebuilt as a centered card (`rounded-lg border bg-surface shadow-lg`) with labeled inputs, visible focus rings, and an error alert, replacing the old bare `<main>`/`<form>` layout that the user specifically named as "clunky, dated" — the pain point this whole phase was reordered ahead of Phase 76 to address. Uses the new `--dms-accent-fg`/`--color-accent-fg` token (added this session, see the ADR) on the primary button instead of a hardcoded `text-white`, after a WCAG contrast failure was found live in the dark and high-contrast themes. Live-verified in all three themes plus the error/focus states, and against the real running Docker container.

**Correction (Post-Roadmap Phase 75 Session 3, [ADR 0220](../adr/0220-office-addin-tailwind-rollout-primary-button-border-fix.md))**: the login submit button was silently relying on the browser's default unstyled `<button>` border (invisible in a screenshot, found via `getComputedStyle()` while verifying `office-addin`'s rollout) — fixed with an explicit `border-0`.

**Correction (Post-Roadmap Phase 75 Session 3 continuation, [ADR 0221](../adr/0221-migration-console-tailwind-rollout-form-font-inherit-fix.md))**: login inputs were rendering in `Arial` instead of this app's own font stack (no global `input,select,button,textarea{font:inherit}` reset existed) — fixed with a small `@layer base` addition to `globals.css`. This is a surgical fix only; this app's own full Tailwind rollout beyond the login page is still its own future dedicated session, not attempted here.

**Since Post-Roadmap Phase 75 Session 7** ([ADR 0224](../adr/0224-admin-ui-tailwind-shell-conversion-dead-banner-css-fix.md)): this app is large enough (~50 components, ~10,800 lines) that its rollout is split across multiple sessions rather than one pass. This session converts only the shell/chrome layer (`AdminShell`, `AdminSidebar`, `ThemeSwitcher`, `LocaleSwitcher`, `InstallationSwitcher`, `MaintenanceBanner`, `LicenseStatusBanner`, `RequireAuth`, `RequireCapability`, `DashboardWidgets`, the home and login pages) to Tailwind utilities — the ~15-30-file-shared classes (`.card`/`.data-table`/`.badge`/`.form-grid`/etc.) stay untouched, each their own future session's job. Found and fixed two real, pre-existing dead-CSS-class bugs as a byproduct: `.maintenance-banner` and `.license-banner` were both never actually defined in `globals.css` — this app's two most important warning banners have been rendering as plain unstyled text. Also found, via the same computed-style probe technique, a subtler defect: the sidebar's group-toggle button had no explicit text-color utility and was rendering in the browser's default black rather than the theme's `--dms-fg`, invisible in light theme but a real bug in dark/high-contrast.

**Since Post-Roadmap Phase 75 Session 8** ([ADR 0225](../adr/0225-admin-ui-shared-class-bulk-conversion-strategy.md)): converted the four single-purpose classes shared across nearly every one of this app's ~30 pages (`.hint`/`.error-text`/`.empty-state`/`.actions`) via a mechanical bulk find-and-replace across all their consumers (32/33/31/19 files respectively, confirmed via grep to be used only standalone), then deleted the four rules from `globals.css`. This changes the rollout strategy going forward: admin-ui's remaining CSS is almost entirely shared "component classes" (`.card`/`.data-table`/`.badge`/`.form-grid`/etc.) reused across most pages, not per-page bespoke CSS, so future sessions convert one shared class (or group) across all its consumers at once rather than one route at a time.

**Since Post-Roadmap Phase 75 Session 9** ([ADR 0226](../adr/0226-admin-ui-card-conversion-shared-component-class-boundary.md)): converted `.card` the same way (57 occurrences across 31 files). Found and fixed a real layout regression Session 8 introduced: converting `.hint` silently broke `.form-grid > .hint`'s "span the full grid width" rule for 7 elements across 6 files — fixed each with `col-span-full`. **This is admin-ui's Tailwind rollout stopping point**: the remaining shared classes (`.data-table`, `.badge`, `.form-grid`, `.attribute-row`, `.checkbox-group`, `.layout-row`, `.layout-field`, `.deletion-reason-catalog*`) all involve descendant/compound selectors (table cells, nested labels, modifier variants) and are kept deliberately as permanent shared component classes — see the ADR for the full reasoning. admin-ui's rollout is considered functionally complete.

**A second, more subtle high-contrast bug found and fixed live, hiding behind the first one — the same class already found in `user-ui` (Phase 49 Session 2)**: a pre-existing override, `:root[data-theme="high-contrast"] .sidebar-link-active { color: #000000; }`, forced the active sidebar nav link's text to black specifically to stay legible against this app's own then-yellow `--dms-accent-bg`. Once the shared file's corrected value (`#000000`, matching ADR 0135) is adopted, that same override would instead pair **black text on a now-black background** — a new illegible pairing occupying the exact spot the original bug used to. Removed rather than carried over; confirmed live via Playwright (logging in, navigating to a sidebar-linked page, and inspecting the active link in high contrast shows normal white-on-black text, matching how light/dark already worked). Component-level hardcoded spacing/radius/font-size values throughout the rest of `globals.css` were also switched to the new scale tokens where they matched a step.

## i18n: English Translation + Locale Switcher (Concept 8, Phase 47 Session 4, [ADR 0167](../adr/0167-locale-switcher-pattern-and-office-addin-host-locale.md))

Own `src/i18n/en.json` — a full English translation of all 754 keys in `de.json` (the largest dictionary of all six apps), reusing the same established German→English glossary as `user-ui`'s own P47-S3 translation (`Umlaufmappe` → "case binder", `Aussonderung` → "records disposal", `Kennzeichen` → "file reference number"), programmatically verified against `de.json`'s flattened key set and every `{placeholder}` variable before touching any code (0 missing/extra, all placeholders matched). `I18nProvider`'s previously-static `locale` prop replaced by a new `LocaleProvider`/`useLocale()` (`lib/locale-context.tsx`) — the same hydration-safe pattern proven in `process-designer` (P47-S1). A new `LocaleSwitcher.tsx` mirrors `ThemeSwitcher.tsx` and is wired into `AdminShell.tsx`'s header, next to `InstallationSwitcher`/`ThemeSwitcher`.

**Provider ordering differs from every other app this phase**: `admin-ui` uniquely has an `InstallationProvider` (ADR 0008, multi-installation) whose `activeInstallation` the installation-scoped `AuthProvider` itself depends on (`auth-context.tsx` imports `useInstallation`) — `InstallationProvider` must therefore stay the outermost provider, unlike the other apps' now-outermost `AuthProvider`. The new tree is `InstallationProvider > AuthProvider > LocaleProvider (renders I18nProvider internally) > ThemeProvider`, replacing the previous `I18nProvider > InstallationProvider > AuthProvider > ThemeProvider` — confirmed safe because `InstallationProvider` itself renders no UI and calls neither `useI18n()` nor `useAuth()`.

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
| AD group→role mapping (since **Phase 50 Session 5**) | `GET/POST /api/auth-service/ad-group-mappings`, `DELETE .../{id}`, `GET/POST /api/auth-service/ad-group-composite-rules`, `DELETE .../{id}`, `GET/PUT /api/auth-service/ad-group-mappings/default-role` |
| Fine-grained user tracking (5.5, since **Phase 52 Session 1**) | `GET/PUT /api/auth-service/user-tracking-config/{principal_id}`, `GET /api/auth-service/user-tracking-sessions`, `GET/PUT /api/auth-service/user-tracking-retention-config` |
| Four-eyes settings (since **Post-Roadmap Phase 22 Session 3**) | `GET /api/permission-service/approval-config`, `PUT .../{action_type}` |
| Object types | `GET/POST/PUT/DELETE /api/object-type-service/object-types`, `GET/PUT/DELETE .../object-types/{id}/layouts/{purpose}` (since P5b-S3) — since **P7-S3** additionally `default_archive_after_days`/`archive_encryption_enabled` in the create/update payload (5.6) |
| Registry | `GET /api/registry-service/instances` |
| Theme and locale preference | `GET/PUT /api/auth-service/me/preferences` (since P4-S6, `locale` since Phase 47 Session 4) |
| OCR settings | `GET/PUT /api/ocr-service/config` (since P5b-S5) |
| Storage guard | `GET/PUT /api/storage-service/guard-config`, `GET /api/storage-service/guard-status` (since P5b-S6), `POST /api/storage-service/guard-status/{target_id}/reidentify` (since P5c-S2), `PUT /api/storage-service/guard-status/{target_id}/config` (since **Post-Roadmap Phase 22 Session 7**) |
| Operational parameters (Storage, since **Post-Roadmap Phase 22 Session 6**) | `GET/PUT /api/storage-service/operational-config` |
| Signature connectors (since **Post-Roadmap Phase 22 Session 6**) | `GET/PUT /api/signature-service/signature-config` |
| Not-shutdown / maintenance mode | `GET /api/permission-service/maintenance-mode` (since P6-S6, `MaintenanceBanner` + `SuperuserBreakGlass`), `POST /api/permission-service/maintenance-mode/trigger`, `POST /api/permission-service/maintenance-mode/lift` (both since P6-S6, `SuperuserBreakGlass`) |
| Retention & deletion register | `GET/PUT /api/document-service/retention-config`, `GET/PUT /api/document-service/trash-config`, `GET /api/document-service/deletion-register` (all since P7-S1, `RetentionSettings`/`DeletionRegister`); since **P7-S1b** additionally the same three against `folder-service` (own, independent configs) |
| Standard reports | `GET /api/reporting-service/reports/{document-volume,open-workflow-tasks,storage-usage,user-activity}` + `.../export?format=csv\|pdf`, `GET/POST/DELETE /api/reporting-service/report-schedules`, `GET /api/reporting-service/report-runs/{id}/download` (all since P7-S2b, `ReportsView`) |
| Forensic trace & audit depth | `GET /api/reporting-service/forensic-trace` + `.../export?format=csv\|pdf` (since P7-S2c, `ForensicTraceView`), `GET/PUT /api/document-service/audit-trace-config`, `GET/PUT/DELETE /api/document-service/audit-trace-role-overrides/{role}` (since P7-S2c, `AuditTraceSettings`) |
| Records disposal & archiving | `GET /api/archival-service/archival-transfers?status=...`, `POST .../archival-transfers/{id}/retrieve` (both since P7-S3, `ArchivalTransfersView`); since **P7-S3b** additionally `GET /api/archival-service/case-archival-transfers?status=...`, `GET .../case-archival-transfers/{id}/package`, `GET`/`PUT /api/case-service/case-archival-config` (`CaseArchivalSection`); since **Post-Roadmap Phase 20 Session 7** additionally `POST .../archival-transfers/{id}/retry`, `POST .../case-archival-transfers/{id}/retry`; since **Post-Roadmap Phase 22 Session 1** additionally `POST /api/document-service/documents/{id}/archive-request` |
| Processing failures | `GET /api/notification-service/notifications?status=failed_permanent`, `POST .../notifications/{id}/retry`, `GET /api/rendering-service/renditions?status=failed_permanent`, `POST .../renditions/{id}/retry`, `GET /api/ocr-service/ocr-results?status=failed_permanent`, `POST .../ocr-results/{id}/retry` (all since **Post-Roadmap Phase 20 Session 7**, [ADR 0083](../adr/0083-admin-ui-processing-failures-visibility.md), `ProcessingFailuresView`); since **Phase 40 Session 3**, also `GET {FEDERATION_HUB_BASE_URL}/handovers?status=delivery_failed`/`?status=result_delivery_failed`, `POST .../handovers/{id}/retry` — called directly against the hub, NOT via `/api/{service}/...` through the gateway (see "Processing Failure Visibility" above) |
| Query console | `GET /api/query-service/query/events?actor=&subject=&event_type=&since=&until=` (since **P8-S1**, `QueryConsoleView`); since **P8-S2b** additionally `GET/POST /api/query-service/manipulation-mode/{status,activate,deactivate}`, `POST /api/query-service/manipulate/{dry-run,execute}`, `GET /api/permission-service/approval-requests?status=pending`, `POST /api/permission-service/approval-requests/{id}/approve` (the latter two already-existing endpoints, reused) |
| Configuration packages | `GET /api/config-service/config/export`, `POST /api/config-service/config/compare`, `POST /api/config-service/config/import` (all since **P17-S1**, `ConfigPackages`) |
| Cross-installation config compare (7.5, since **Phase 52 Session 2**) | `POST /api/auth-service/login` and `GET /api/config-service/config/export` against the OTHER installation's own gateway URL directly (`loginAt`/`exportConfigAt`, bypassing this module's own `gatewayBaseUrl`), then `POST /api/config-service/config/compare` against the active installation as usual — see "Cross-Installation Config Compare" below |

## Auth State

`src/lib/auth-context.tsx` — installation-bound since P4-S5 (see above), otherwise like the User UI: `localStorage` tokens, proactive refresh — deliberately duplicated instead of shared (ADR 0006: no shared business logic between independently deployable frontend apps). Since **P6-S5**, additionally loads `permissions: string[]` (`getEffectivePermissions`, Permission Service) right after `user` — native domain admin capabilities (4.6), deliberately separate from `user.realm_roles` (Keycloak), see [ADR 0023](../adr/0023-superuser-breakglass-and-domain-admin-accounts.md).

## Internationalization (Concept 8, since P4-S3)

Like the User UI: `src/i18n/de.json` + `useI18n()` (see [ADR 0007](../adr/0007-frontend-i18n-preparation.md)). Own dictionary, since Admin UI terms (user management, object type editor, registry, installations) are entirely different from the User UI terms.

**Gender-neutral messaging pass (Post-Roadmap Phase 31 Session 8, [ADR 0119](../adr/0119-accessibility-pass-badges-gender-neutral-text-tagged-pdf-warning.md))**: masculine-generic person/role nouns in flowing prose were reworded following this codebase's own already-established convention (`"Mitarbeitender"`/`"ausführende Person"` predate this session) — substantivized participles for standalone category labels (`"Nutzer"` → `"Nutzende"`: `nav.users`, `users.pageTitle`/`sectionTitle`/`formLabel`/`empty`/`createError`, `license.users`) and `"Person"`/`"Personen"` for prose describing what an individual did or is allowed to do (`groups.hint`, `reports.filterActor`/`actor`, `forensicTrace.hint`/`filterActor`, `queryConsole.filterActor`, `auditTraceSettings.hint`, `retentionSettings.deletionReasonCatalogEmpty`). Deliberately excludes field/credential labels (`"Benutzername"`) and compound technical nouns (`"Nutzerkonto"`, `"Empfänger-Domain"`) — not a gendered-language issue, see ADR 0119 for the full per-string reasoning.

## Authorization

**Enforced since P6-S5 for a first case** (4.6): `/users/` checks the capability `admin.user_management` — `AdminSidebar` hides the nav entry without this capability, `RequireCapability` (a sibling of `RequireAuth`) additionally protects the route itself (defense in depth against directly called URLs), and the actual backend endpoint (`auth-service` `/users`) enforces it independently of UI state.

**Full alignment since Post-Roadmap Phase 38 Session 3** ([ADR 0148](../adr/0148-admin-ui-authorization-full-alignment.md)): an audit found the previous state understated by the wording above — of ~27 admin pages, only 3 (`/users/`, `/license/`, `/teamspaces/`) had the full `RequireAuth`+`RequireCapability` pattern, and critically, most of the remaining pages' corresponding BACKEND endpoints had no RBAC at all either, not merely a missing frontend affordance for an already-enforced capability. This session closes both gaps together, page by page:

- **`RequireCapability` wrapper added** (capability newly enforced server-side in the same session, see the respective service's own docs for the capability/role name): `/object-types/`, `/kennzeichen-settings/` → `admin.object_config` (reused, was already enforcing workflow-service's BPMN/DMN definitions, just never wired up in object-type-service itself); `/upload-settings/`, `/export-settings/`, `/audit-trace-settings/`, `/share-link-settings/` → `admin.document_config` (new, one shared capability across all five document-service settings pages); `/signature-config/` → `admin.signature_config` (new); `/storage-guard/`, `/storage-operational-config/` → `admin.storage` (reused — seeded since P9-S1, never enforced anywhere until this session); `/retention-settings/` → `admin.retention` (new); `/delegations/`, `/approval-settings/` → `admin.user_management` (reused); `/email-templates/` → `admin.notification_config` (new).
- **`RequireCapability` wrapper added for an ALREADY-enforced capability** (previously sidebar-only, per the old "deliberately forgoes an additional wrapper" text below, now judged inconsistent with every other page and closed): `/config-packages/` (`admin.object_config`, unchanged — enforced by `config-service` since P17-S1), `/query-console/` (`admin.query_console`, unchanged — enforced since P8-S1).
- **Deliberately left alone**: `/reports/`, `/forensic-trace/` (reporting-service gates via the "everyone" group per ADR 0072, by design — not a domain-admin concern), `/ocr-settings/` (same, ADR 0073), `/registry/`, `/installations/`, `/processing-failures/`, `/deletion-register/`, `/archival-transfers/`, `/superuser/` (read-only, already server-gated, or judged low-priority/adequate as-is).

`AdminSidebar`'s `requiresCapability` map was extended to match every newly-wrapped page, so the nav entry and the route-level guard now always agree.

**`/processing-failures/` partially closed since P63-S3**: the blanket "no single clean capability to
gate the whole page on" reasoning above no longer fully holds — `notification-service`'s `GET
/notifications` gained `admin.notification_read` in Post-Roadmap Phase 59 Session 1, so one of the
page's four sections now DOES have a real backend gate. Rather than wrap the whole page (which would
incorrectly hide the other three, still-ungated sections from a caller missing only that one
capability), `NotificationFailuresSection` gates ITSELF: checks `permissions.includes("admin.
notification_read")` and shows a graceful missing-permission message instead of calling the API, while
`RenditionFailuresSection`/`OcrResultFailuresSection`/`HandoverFailuresSection` and the page's
`AdminSidebar` nav entry remain exactly as ungated as before. See "Processing Failure Visibility" below.

**Since P6-S6 additionally for not-shutdown (4.8)**: `SuperuserBreakGlass` only shows the trigger form if `permissions` (from `auth-context.tsx`) contains the capability `system.not_shutdown.trigger` — purely client-side UX anticipation, actual enforcement happens at the Permission Service (`403` without the capability). The lift button is additionally tied to a second condition that is not purely role-based: it only appears if the currently logged-in principal (`user.sub`) matches `status.principal_id` (the active superuser) — any other person with `system.not_shutdown.trigger` does not see the button, even though they would be allowed to trigger maintenance mode (triggering and lifting are deliberately different permissions, 4.8).

## Build & Delivery

Two-stage Docker image (`apps/admin-ui/Dockerfile`), identical to the User UI. `NEXT_PUBLIC_GATEWAY_BASE_URL` as a build arg (initial value of the "Local" installation), overridable via `ADMIN_UI_GATEWAY_BASE_URL` in `infra/.env`. Port `3001` (User UI: `3000`). Additional installations are added at runtime via `/installations/`, not via a rebuild.

**Since Phase 49 Session 3**: the build stage's `WORKDIR`/`COPY` layout changed to mirror the actual repo shape (`/repo/apps/admin-ui/`, plus `/repo/libs/dms-ui/`) instead of flattening this app into `/app` — needed so `globals.css`'s new relative `@import` of `libs/dms-ui/tokens.css` resolves identically to a local `npm run build`, same fix as every other app in Phase 49. The two existing build args (`NEXT_PUBLIC_GATEWAY_BASE_URL`, `NEXT_PUBLIC_FEDERATION_HUB_BASE_URL`) carried over unchanged.

## Fleet Orchestration (P88-S1, [ADR 0242](../adr/0242-admin-ui-fleet-orchestration-and-rollout-lifecycle.md))

Once a fleet token has been accepted, `FleetOrchestration.tsx` adds four sections below the managed
installations. All of them use the token from the last successful "Laden"; the server takes the acting
operator from it.
- **Gruppen (Wellen):** create and delete groups, add installations through a select (only those not
  yet in the group), and remove them through the × on their chip.
- **Update-Pläne:** a plan has a name, a version and ordered steps. Each step is a Bestätigung (`gate`,
  optionally "mit Freigabe") or a Prüfung (`verify`), and steps can be moved up or down. A step without
  a name is refused before the request. The list shows each plan's steps.
- **Rollouts:**
  - Creating one takes a name, a plan, an optional group, checkboxes to exclude group members or add
    other installations, and a live "Zielkreis: n Installation(en)".
  - Each rollout shows its state (Entwurf, Läuft, Abgeschlossen, Abgebrochen), the plan, who started it
    and when, and one row per installation: step `i/n` with its type, and the run state with its error
    or proposer.
  - The actions follow status and step type (table in ADR 0242). "Ergebnis melden" and "Ablehnen" open
    an inline form, and the row's buttons hide while it is open. "Freigeben" is disabled, with the
    reason, for the operator who proposed the step.
  - "Abbrechen" on a running rollout; "Entfernen" on any other.
- **Operatoren:** the list for everyone. Admins can also mint an operator (the token is shown once) and
  revoke one; `bootstrap` has no revoke button.

Every section shows its own errors, including the server's `409` texts (for example "Gruppe wird von
einem Rollout verwendet"). At 390px the tables scroll inside their containers.

## No Actor Fields in Requests (P85-S5, [ADR 0240](../adr/0240-acting-identity-from-verified-principal.md))

The client no longer sends who is acting (`created_by`, `deleted_by`, `approved_by`, `locked_by`, …):
the services take it from the verified token. The API helpers lost those parameters, and the
components no longer pass the user name into them. Super-user activation, approve/reject, emergency shutdown and lift, and the user-tracking toggle no longer pass `user.sub`/`user.username`.

## Registry by Service Type (P87-S3, [ADR 0241](../adr/0241-service-instance-replacement-and-expected-replicas.md))

`RegistryOverview.tsx` shows one row per service type (`GET /replicas` joined with `GET /instances`):
- **Instances:** reachable, plus how many are draining or unreachable.
- **Expected:** the range (`2–3`, `≥ 2`, a single number, or `—`) and its source (Admin/Standard).
- **State:** OK, Zu wenige, Verschwunden, Zu viele or Nicht überwacht (a neutral badge), with "seit
  <date>". A state still inside its debounce shows as `wechselt zu „…“`.

**Rows:**
- A type expands to its instances (id, version, address, last seen, status).
- With `admin.monitoring`, a type row has "Erwartung", which opens min/max plus "Auf Standard
  zurücksetzen" for an admin override.
- An unreachable instance has "Vergessen". Without the capability the page is read-only; the server
  checks it either way.
- A hint says that replaced instances are forgotten automatically and that each forget is in the audit
  trail (`registry.instance.forgotten`, 279 of them on the dev stack after P87-S2/S3).
- At 390px the table scrolls inside its container (ADR 0238); type names stay on one line.

**Fleet view:** below each installation's status it shows "Replikas im erwarteten Bereich" or the
out-of-range types from the installation's replica summary.

## Sensor Alerts (P88-S6, [ADR 0245](../adr/0245-sensor-alerting-thresholds-downtime-and-notification.md))

The alerting UI is on the sensors page (`/sensors/`, "Monitoring-Sensoren"), in `SensorAlerts.tsx`;
monitoring-service evaluates (P88-S5).
- **"Aktive Alarme"** at the top: rules in warning/error, worst first, with value, thresholds, since
  when, and "Mail unterdrückt (Wartung)" while a window covers them.
- **Column "Alarm"** in "Gruppen und Sensoren": the worst level of a sensor's enabled rules, also per
  group in the group row. A link ("einrichten" or "n Regel(n)") expands the sensor to its rules.
  - Each rule shows level, aggregation and filter, the value, the thresholds in one line ("Warnung > 80
    · Fehler > 95") and since when.
  - With `admin.monitoring`: Bearbeiten, Löschen, Alarmregel hinzufügen.
- **Rule form:**
  - name, aggregation, a label filter (key=value rows), the four thresholds, the two repeat counts
    (with the rule explained), active;
  - a warning when the sensor is currently off.
  - **"Aktuellen Wert prüfen"** calls `POST /alert-rules/preview`: the value now and the level it would
    have, or why there is none (no samples; a rate needs two measurements). It also lists the matching
    series, whose label chips take a label over into the filter.
  - Threshold order and repeats are checked before saving.
- **"Wartungsfenster":** a list with state (aktiv / nicht aktiv / deaktiviert), schedule ("Mo, Mi ·
  06:00–07:00", absolute range) and scope. The form covers name, from/until (`datetime-local`), weekdays
  plus a daily time range (spanning midnight explained), all rules or selected ones, and active.
- **Badges:** a new `.badge.warn` (dark yellow on light, light yellow on dark, bordered in high
  contrast) for "Warnung"; error uses `down`, unknown `neutral`.
- **Read-only** without `admin.monitoring`: no edit/add/delete, no window form.

## Mailboxes (P88-S4b, [ADR 0244](../adr/0244-mail-connector-mailboxes-in-a-table-with-encrypted-credentials.md))

`MailboxSettings.tsx` at `/mailboxes/` ("Postfächer", under Verarbeitung) manages mail-connector's
mailboxes, which moved from `DMS_MAILBOXES` into a table in P88-S4a. It needs `admin.mail_config`
(`domain-admin-mail`); the nav entry, the route guard and the server all check it.
- **Table:** per mailbox:
  - name and id, kind (central or department, with the group name), the connection (protocol,
    host:port, TLS, username, "kein Passwort" if none is stored);
  - the last retrieval (ok or error with the server's reply, time) and the message count;
  - Bearbeiten, Deaktivieren/Aktivieren, and Löschen only without messages.
- **Form:**
  - id (immutable when editing), name, kind, and the responsible group (a select from
    permission-service's groups, or a text field if they can't be listed);
  - protocol, host, port (placeholder = the default for protocol and TLS), TLS, IMAP folder;
  - username, password, and active.
  - **The password field is write-only:** it stays empty when editing, shows "unverändert" and isn't
    required when one is stored, and an empty field keeps the stored secret.
  - **"Verbindung testen"** logs in with the form as it is (falling back to the stored password) and
    shows "Anmeldung erfolgreich" or the server's reply.

## Realm Roles (P88-S4b)

`RealmRoleAssignments.tsx` is a section of the user management page ("Realm-Rollen
(Anwendungszugang)"). It is the UI for auth-service's realm-role assignment endpoints (Phase 78):
before, realm roles such as `dms-poststelle` could only be assigned in the Keycloak admin console.
- **Role-first:** a role expands to its members (`GET …/assignments`). A member is added by username,
  with the users who don't have the role yet suggested, and removed with the × on the chip.
- **Filter:** above ten roles. The dev realm holds about 130 roles left behind by test suites, a
  hygiene point for P93-S3.
- **The hint explains the difference to the permission roles below:** application access versus
  permissions on resources. A change applies from the person's next login.

## Object-Type Rules (P88-S3)

`ObjectTypeRules.tsx`, inside `ObjectTypeEditor` after the attributes, edits the three rule kinds the
constraint engine evaluates. Before, `naming_constraints`/`conditions` were passed through raw, and
`status_transitions` were not sent at all: `updateObjectType` lacked the field, so every save reset them
on the server. Both API helpers now send all three, and creating a type can set them too.
- **Namensregel:**
  - checkboxes for the attributes a name requires (`mustContain`);
  - the pattern, with one button per attribute that inserts `{attribute}`;
  - a sample-name tester. `nameMatchesPattern` mirrors `_validate_naming`: literal text, placeholders
    match any non-empty text, the extension is ignored.
  - Undeclared placeholders are named.
- **Bedingte Pflichtfelder:**
  - each condition reads as a sentence: "Wenn [Attribut] [Vergleich] [Wert] dann Pflichtfeld
    [Attribut]", serialized as `{"if": "Betrag >= 10000", "then": "require:Kostenstelle"}`;
  - only attributes whose name the engine's condition parser can read are offered;
  - a stored condition the form can't represent stays unchanged and is shown read-only, with a remove
    button.
- **Statusübergänge:**
  - von/nach with the statuses a service actually checks today (`open`, `closed`) suggested through a
    datalist, free text allowed;
  - checkboxes for the attributes the transition requires.
- **Undeclared attributes:** a rule referencing an attribute that is no longer declared shows it as
  "(nicht deklariert)" instead of dropping it silently.
- **Client checks before saving** (`rulesError`): incomplete conditions, empty or equal von/nach, and a
  transition requiring an undeclared attribute (which the server would refuse with `422`).

The page also had a pre-existing 2px overflow at 390px: the layout designer's button row didn't wrap. It
now wraps.

## Embedded-Metadata Mappings (P90-S5, [ADR 0258](../adr/0258-embedded-metadata-mappings-on-upload.md))

`ObjectTypeMetadataMappings.tsx`, in `ObjectTypeEditor` after the rules and for document types only,
edits "Metadaten aus Dateien übernehmen": one row per mapping, Dateieigenschaft → Attribut, Übernahme
("Nur wenn leer" / "Immer (überschreibt Eingabe)").
- The attribute select offers only attributes that can hold the chosen property: text attributes for
  every property, date attributes for Erstellt/Geändert. A stored target that no longer fits stays
  visible as "(nicht deklariert)".
- **Client checks before saving** (`mappingsError`, mirroring the server's): no attribute chosen, an
  undeclared attribute, a type mismatch, an attribute mapped twice.
- `createObjectType`/`updateObjectType` send `metadataMappings`; a folder type always sends `[]`.
  `updateObjectType` omits the field when it isn't given, and the server then keeps the stored
  mappings.

## Monitoring Sensors (P88-S2, [ADR 0243](../adr/0243-sensor-group-and-cost-overrides-and-admin-page.md))

`SensorSettings.tsx` (`/sensors/`, under Verwaltung) shows monitoring-service's sensor catalog and its
activation config:
- **Grundeinstellung:** "Alles/Nichts überwachen" radios with the active count.
- **Nach Kosten:** switches for Günstig and Teuer.
- **Gruppen und Sensoren:** a table with a header row and switch per group. Each sensor shows its name,
  description, cost badge, services (collapsed to "n Dienste" above three), state with its source
  ("über die Gruppe" …) and its own switch. A filter matches name, description and service.
- **Einstellungen ohne laufenden Sensor:** overrides that no running service matches, each with a reset.

Every switch is Übernehmen/An/Aus: Übernehmen clears the override (`enabled: null`), so the next less
specific level applies. Without `admin.monitoring` everything is disabled and a read-only hint is shown.
At 390px the table scrolls inside its container.

## Names Instead of Principals (P86-S2)

Since P85 (ADR 0240) recorded actors are principals: a Keycloak `sub`, or a technical account's integer
id. `components/PrincipalName.tsx` (`<PrincipalName>` and `usePrincipalName`, built on
`lib/usePrincipalNames.ts`, the same file as in user-ui and reviewer-ui) shows them as usernames with the
raw id on hover. It is used for:
- the forensic trace, query console and user-activity report (`actor`) and the query console's pending
  approvals (`initiated_by`);
- the deletion register (`triggered_by`) and the break-glass maintenance line;
- teamspaces (`created_by`);
- the AD-group default role and the user-tracking config (`updated_by`, plus the tracked person).
- **Actor filters** in the forensic trace and query console accept a username: `resolveActorFilter`
  looks it up (`GET /users/lookup`, which since P86-S2 also finds technical accounts) and queries by the
  principal. An unknown name is sent as typed, so it still matches older events that carry usernames.
  The user-activity report's filter still matches only the value as recorded.
- **Installation switch:** the name cache is cleared, because integer ids repeat across installations.
- **Forensic trace:** no longer sends `queried_by`, which the server ignored (it uses the verified token).

**Form spacing (P86-S2, P81-S1 item 4):** `.form-grid` gaps are 16px between rows and 12px between
columns (were 8px). The element after a form, usually its table, starts 16px below it; before, a create
form sat directly on its table's header. `.layout-row`/`.layout-field` went to 12px. The new
`.form-grid + *` rule is in `@layer base`, so a margin utility on that element still wins.

## Account Menu (P86-S1, [ADR 0238](../adr/0238-narrow-width-patterns-for-all-apps.md) Decision 2)

The header's right side is one button: the signed-in user's name (from 640px; an icon below) with a
chevron. It opens the account dropdown: the installation switcher (only when more than one
installation is configured, via `AccountMenu`'s `leading` slot), language, theme and log out, each at
least 40px tall. Before, wide layouts showed all of these inline next to the name. The same
`AccountMenu.tsx` is copied into reviewer-ui, migration-console, process-designer and user-ui (ADR 0006).
`e2e/login.spec.ts` logs out through the menu at desktop width.

## Tests

- `npm run typecheck` / `npm run lint` / `npm run build`.
- `npm test` (Vitest + Testing Library, **299 tests since P71-S3** — +3 in `object-type-editor.test.tsx`
  for the new `reference_target` picker: creates a reference attribute with its target set, omits
  `reference_target` when left unset (a reference attribute may stay format-only, see ADR 0193's own
  "Rationale"), loads an existing `reference_target` value into the form for editing. See "Object Type
  Editor" above/`docs/adr/0193-cross-service-reference-validation.md`'s "Consequences".

  **290 tests since P67-S1** — +7 in the new `fleet-management.test.tsx`
  (mocked API): no list before a key is loaded, loads/lists once submitted, unreachable/invalid-key state,
  registers an installation and shows the one-time plaintext key, deletes and reloads, checks status of
  all installations, pushes a license token. Previously 283 tests since P63-S3 — +1 in `processing-failures.test.tsx`:
  a new test for `NotificationFailuresSection`'s missing-permission state (shows the graceful message,
  calls no API, other three sections unaffected). **Also fixed in this session, no test-count change**:
  the long-standing `"retries a failed handover (result leg) and reloads"` flake (first noted Phase 47
  Session 4, re-confirmed-but-deferred by several sessions since) — it never filled the operator-key
  input ADR 0162 made required for the retry button to be clickable at all; fixed by filling it and
  asserting the real two-arg `retryHandover` call. Zero known failures in the suite as of this session.
- **New `e2e/fleet-management.spec.ts` (P67-S1)**: Playwright, against the real running stack — logs in,
  loads the fleet list with a real operator key, registers an installation, asserts the one-time plaintext
  key is shown, asserts the row appears, deletes it, asserts it's gone. **Found, during this session, four
  pre-existing `e2e/` specs (`login.spec.ts`, `user-management.spec.ts`, `object-types.spec.ts`,
  `email-templates.spec.ts`) that currently fail against this stack** — confirmed via trace inspection to
  be stale UI-text assertions unrelated to fleet management (e.g. `login.spec.ts` looks for a nav link
  named "Nutzer & Rollen", the app's actual current text is "Nutzende & Rollen"), not a real regression.
  Not fixed here, flagged for a future session.

Older history: 282 tests since Phase 58 Session 2 — +2 in
  `config-compare.test.tsx`: the new global ignore-regex field is sent as `ignore_regex: {"*":
  value}`, and expanding a differing item's new `<details>` disclosure reveals its per-field
  `{base, compare}` table — see "Cross-Installation Config Compare" above. Before that, 280 tests since P53-S3 — +1: a new
  `reports-view.test.tsx` test ("shows a visible status for each schedule's last delivery attempt")
  for the new `last_status`/`last_error`-driven status column in `ReportsView.tsx`'s schedule table
  (`.badge.ok`/`.badge.down`/never-run hint, incl. the `title` tooltip carrying `last_error` on the
  failed badge) — see `docs/services/reporting-service.md`. The one known pre-existing, unrelated
  `processing-failures.test.tsx` failure (noted below, Phase 47 Session 4) is still present, confirmed
  unrelated to this session's diff. Before P53-S3, 279 tests since P53-S1 — +1: `ad-group-mappings.test.tsx`
  gained a test for the new `AdGroupMappingDefaultRoleActionResult` envelope's `pending_approval` path
  on `handleSaveDefaultRole` (see `docs/services/auth-service.md`). Before P53-S1, **278 tests since Phase 52 Session 2** — +3: a new
  `config-compare.test.tsx` for the new `ConfigCompare.tsx` component — the empty-state hint with only
  one known installation, a full login-at-the-other-installation/export/compare round trip (asserting
  `loginAt`/`exportConfigAt` are called against the OTHER installation's own `gatewayBaseUrl`, never the
  active one's), and a login-failure error path. Before Phase 52 Session 2, 275 tests since Phase 52
  Session 1 — +12: a new `user-tracking.test.tsx` for the new `UserTracking.tsx` component (+7 —
  section visibility per capability, config lookup/toggle, sessions list/filter, retention load/save,
  client-side retention validation, a load-error path) and a new `require-capability.test.tsx` (+5 —
  the pre-existing single-capability behavior unchanged, plus the new array/OR form this session added
  to `RequireCapability.tsx` itself, needed because this is the first admin page whose backend gates
  split across two independent capabilities with no single one covering the whole page). **The same
  pre-existing, unrelated `processing-failures.test.tsx` failure noted below (Phase 47 Session 4,
  missing ADR-0162 operator-key input) is still present, confirmed unrelated to either session's diff.**
  Before Phase 52 Session 1, 263 tests since Phase 50 Session 5 — +10: a new
  `ad-group-mappings.test.tsx` for the new `AdGroupMappings.tsx` component — empty states, listing,
  create/reload for both the simple-mapping and composite-rule forms, the client-side ≥2-distinct-groups
  validation ahead of the backend's own `422`, delete/reload for both, the `pending_approval` response
  path (no reload, matching `UserManagement.tsx`'s established handling), the default-role load/save
  round trip, and a create-error path. Before that, 253 tests since Phase 47 Session 4 — +3: a new
  `locale-context.test.tsx` (default/cache-after-mount/persistence, mirroring `theme-context.test.tsx`'s
  own structure, same `InstallationProvider > AuthProvider > LocaleProvider` wrapping order the
  restructured `layout.tsx` now uses). **One pre-existing, unrelated failure found during this
  session's own full-suite run, not caused by this session's diff and left unfixed as out of scope**:
  `processing-failures.test.tsx`'s "retries a failed handover (result leg) and reloads" fails
  consistently (also in isolation, confirming it isn't cross-file test pollution) — the test never
  fills in the "Hub-Operator-Schlüssel" field ADR 0162 (Phase 44 Session 1) made required for a
  handover retry, so the retry call never fires. Predates this session (this session touched no file
  the test or `ProcessingFailuresView` depends on) and is unrelated to i18n; worth a dedicated fix in
  a future session, not folded into this one.
  Before that, **250 tests since Phase 45 Session 5** — +1: a new
  `admin-sidebar.test.tsx` case confirming the four newly-gated nav entries (superuser, archival
  transfers, forensic trace, reports) hide without their respective capabilities while the
  deliberately-still-ungated `/deletion-register/` entry stays visible; the existing "shows both
  groups expanded by default" case's `mockPermissions` fixture also gained `breakglass.approve`, since
  that test's own default set would otherwise no longer show the now-gated "Superuser Break-Glass"
  entry it asserts on — verified against the real, exact live count via a full test run (`npm test`,
  250 total, one pre-existing unrelated failure in `processing-failures.test.tsx`'s handover-retry
  test not touched by this session, see "Open Points"). Before that, **249 tests since Post-Roadmap Phase 41 Session 2** — +2:
  two new `ObjectTypeEditor` tests for the `personal_data` per-attribute checkbox (5.2, [ADR 0156](../adr/0156-attribute-pseudonymization-reversible-vault.md)) — submitted when creating an object type, and correctly loaded per-attribute when editing an existing one with a mix of flagged/unflagged attributes. Before that, 247 tests since Phase 40 Session 3 — +3:
  `processing-failures.test.tsx` gained a fourth section's coverage (`HandoverFailuresSection` —
  lists failures from both independent legs via `listHandovers`, called directly with no gateway
  token unlike the other three sections; unreachable state; retry-then-reload for the result leg,
  same-endpoint dispatch), see "Processing Failure Visibility" above. Before that, 244 — **+3 from
  Phase 40 Session 1** (`storage-guard.test.tsx`'s new decommission checkbox: confirms then
  decommissions, confirmation-dismissed leaves it untouched, reactivation asks no confirmation, see
  `docs/services/storage-service.md`) — not previously recorded in this file's own count chain
  below, corrected here rather than left silently stale. Before that, 241 — since **Post-Roadmap
  Phase 38 Session 3** (see "Authorization" above): 1 new `admin-sidebar.test.tsx` case (hides "Objekttypen"/"Kennzeichen-Einstellungen" without `admin.object_config`) — two existing cases updated in place: the default test-permission set now includes `admin.object_config` (so "Objekttypen" stays visible in the baseline "shows both groups" case), and the "hides users, but X stays visible" contrast test swapped its "X" from "Objekttypen" (now itself gated) to "Registry" (still carries no capability at all); before that, 240 tests — since **Post-Roadmap Phase 36 Session 3** (see "Query Console"/"Forensic Trace & Audit Depth" above): 2 new `query-console-view.test.tsx` cases for the reject button (rejecting a pending approval with a reason via the inline form, cancelling the form without calling the API) and 2 new `forensic-trace-view.test.tsx` cases for the new transparency hint (filtered-count hint when entries were hidden by RBAC, superuser hint) — existing `forensic-trace-view.test.tsx`/`query-console-view.test.tsx` mocks updated in place to include the new `total_before_filter`/`total_after_filter`/`superuser` response fields; before that, 236 tests — since **Post-Roadmap Phase 36 Session 1** (see "Export Settings" above): new test file `export-settings.test.tsx` (4 tests: loading/displaying the configuration incl. the stamp-enabled checkbox, unreachable state for `document-service`, saving changed values across all five fields, the server-side `422` validation message for an invalid `stamp_type`/`stamp_position` combination surfaces via the existing generic error-text pattern) for the new `ExportSettings` — before that, 232 tests — since **Post-Roadmap Phase 35 Session 1** ([ADR 0143](../adr/0143-org-unit-marker-and-org-hierarchy-grant-admin-visibility.md)): 2 new `user-management.test.tsx` cases (creating a group with the new `is_org_unit` checkbox checked, toggling an existing group's flag via the new badge-button and reloading) and 2 new `delegations-admin.test.tsx` cases (the new "Origin" column shows "Selbstverwaltet" for a self-service row and the matching `grant_kind` label for an org-hierarchy-derived one, the new filter checkbox hides self-service rows) — one existing `user-management.test.tsx` case updated in place for `createGroup`'s new `is_org_unit` argument — before that, 228 tests — since **Post-Roadmap Phase 33 Session 3** ([ADR 0137](../adr/0137-axe-core-a11y-test-harness.md)): automated a11y regression net via `jest-axe` (same setup as `user-ui`: registered against Vitest's own `expect`, `color-contrast` disabled for jsdom's lack of real rendering) — 1 new `layout-designer.test.tsx` case running axe against a multi-field row, as a regression net for the P33-S1 `aria-label` fix — before that, 227 tests — since **Post-Roadmap Phase 33 Session 1** ([ADR 0135](../adr/0135-accessibility-audit-five-frontend-apps.md)): 1 new `layout-designer.test.tsx` case (the within-row reorder buttons' new `aria-label`s and the actual field-order swap on click) — before that, 226 tests — since **Post-Roadmap Phase 32 Session 1** ([ADR 0130](../adr/0130-approval-config-self-gated-role-creation-four-eyes.md)): 2 new `user-management.test.tsx` cases for `POST /roles`'s new four-eyes wiring (creates a role and reloads on `status="created"`, pending-approval hint without reload on `status="pending_approval"`) — `createRole`'s return type changed from a bare `Role` to a `RoleActionResult` envelope, mirroring the existing `RoleAssignmentActionResult` pattern; before that, 224 tests — since **Post-Roadmap Phase 31 Session 9** ([ADR 0120](../adr/0120-org-hierarchy-supervisor-dag-groups-as-org-units.md)): 4 new `user-management.test.tsx` cases for the org-hierarchy section (empty state, create + reload, list + delete, chain lookup unioning multiple upward paths) — before that, 220 tests — since **Post-Roadmap Phase 31 Session 8** ([ADR 0119](../adr/0119-accessibility-pass-badges-gender-neutral-text-tagged-pdf-warning.md)): `admin-sidebar.test.tsx`/`forensic-trace-view.test.tsx`/`query-console-view.test.tsx`/`user-management.test.tsx` updated to assert the gender-neutral string variants, no new test cases (a text-content pass, not a new feature) — before that, 204 tests — since **Post-Roadmap Phase 22 Session 7** (see
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
- **No browser available in this development environment** (see `docs/services/user-ui.md`) — every Admin UI gateway call was individually traced via `curl` against the real running Compose stack (since P4-S6 additionally: `GET/PUT /me/preferences` including 422 for an invalid theme value; since P5b-S3 additionally: the triple `PUT .../layouts/{purpose}` sequence sent by `ObjectTypeEditor` on creation with differing display names, as well as a `PUT /object-types/{id}` with `naming_constraints`/`conditions` passed through unchanged, both replicated 1:1; since P5b-S5 additionally `GET/PUT /api/ocr-service/config`; since P5b-S6 additionally `GET/PUT /api/storage-service/guard-config` and `GET /api/storage-service/guard-status` against a real simulated storage device swap; since P5c-S2 additionally `POST /api/storage-service/guard-status/{target_id}/reidentify` against a real storage device swap accepted without a restart; since P5e-S3 additionally `GET/PUT /api/object-type-service/kennzeichen-config` as well as a complete end-to-end run through the gateway with a real login: create an object type with `kennzeichen_format`, create a document with a spoofed client-side `Kennzeichen` (server value wins), `PATCH` without the `dms-admin` role → `403`; since **Post-Roadmap Phase 20 Session 7** additionally verified directly against the newly `document_id`-optional `GET /api/rendering-service/renditions?status=failed_permanent`/`GET /api/ocr-service/ocr-results?status=failed_permanent` — both returned real `failed_permanent` records across multiple documents, stemming from earlier live verifications of this phase, confirming the cross-document filtering with real rather than synthetic data; `GET /api/notification-service/notifications?status=failed_permanent` correctly returned an empty list; the new `/processing-failures/` route is served by the `admin-ui` container). The multi-installation behavior, theme switching, and the new guided forms/the layout designer/the OCR settings page/the storage guard status block/the reference-number fields/the new `ProcessingFailuresView` itself (row/field interactions, badges, conditional fields) are purely client-side and were **verified only via the Vitest component tests, not visually in a browser** — explicitly communicated to the user as a limitation. **Corrected in Phase 45 Session 5**: a real headless-Chromium browser (via `playwright`, already an existing devDependency, driven directly with a small throwaway script since no `chromium-cli` was available in that sandbox — no longer "no browser available") verified the new RBAC gating end-to-end against the real running stack: logged in as `users-admin` (who has `archival.read`/`reporting.forensic_trace`/`reporting.read` via the "everyone" default but not `breakglass.approve`), confirmed the three former stayed visible/accessible while "Superuser Break-Glass" was hidden from the sidebar and a direct navigation to `/superuser/` redirected to `/`; granted `breakglass.approve` and confirmed the page became visible and accessible; revoked the grant again and confirmed it was hidden/redirected once more.

## Open Points

- ~~**`e2e/` specs drifted behind real i18n label changes made in earlier sessions, found in P71-S3**:
  `user-management.spec.ts` (`getByLabel("Nutzername")`, but `formLabel`/field labels are now "Konto
  anlegen" etc.), `fleet-management.spec.ts`, and `email-templates.spec.ts` all failed on a fresh
  container rebuild — none of these were touched by, or related to, this session's own change
  (`ObjectTypeEditor.tsx`'s `reference_target` picker). `login.spec.ts` had the same class of drift
  (a stale "Nutzer & Rollen" link name, now "Nutzende & Rollen") plus a second, independent issue —
  the P69-S2 dashboard widget cards repeat the same sidebar link text in their body, so the unscoped
  `getByRole("link", {name: ...})` locator started matching two elements and violating Playwright's
  strict mode — both fixed in this session, scoped to the sidebar `nav` specifically. The other three
  specs were left stale, out of scope for a session about `object-types`; a future session should
  re-run the full `e2e/` suite fresh and reconcile all of it in one pass rather than chasing one spec
  at a time. Also found and fixed (a live-state issue, not a code change — nothing to commit): this dev
  stack's own documented one-time e2e grant (`e2e/README.md`-equivalent instructions live in
  `apps/process-designer/e2e/README.md`, reused here) had been lost — `users-admin` (principal id
  `"2"`) no longer held `domain-admin-config`/`admin.object_config`, restored via the same grant.~~ —
  **closed in Phase 79 Session 3, with the actual cause turning out different from every prior
  session's own diagnosis**: re-running the full `e2e/` suite fresh found `user-management.spec.ts` was
  the ONLY one with genuinely stale text (fixed, one line). `fleet-management.spec.ts` failed on a
  missing `DMS_FLEET_OPERATOR_KEY` dev-only default in `infra/docker-compose.yml` (never permanently set
  after P67-S1's one-off live verification — now added, matching every other dev secret's `_dev_only`
  convention in that file). `object-types.spec.ts`/`email-templates.spec.ts` both failed because the
  shared `users-admin` account genuinely lacks `admin.object_config`/`admin.notification_config` — not
  UI drift, and not a "lost e2e grant" either (that framing was itself already imprecise: the real fix
  is a DIFFERENT login account per page's own capability, not re-granting `users-admin` a permission it
  was never meant to carry). `object-types.spec.ts` now logs in as the existing `config-admin` account;
  `email-templates.spec.ts` logs in as a new `notification-admin` technical account (`auth-service`'s
  `bootstrap.py`, see `docs/services/auth-service.md`). `fixtures.ts`'s `loginViaUi()` gained an optional
  per-call credentials parameter for this. All 7 `e2e/` specs pass now.
- ~~**Authorization enforced only for `/users/`**~~ — **stale by Phase 45 Session 5**, and actually closed then: this bullet had already fallen behind several intervening sessions that quietly added `RequireCapability` gating to most other pages (`approval-settings`/`audit-trace-settings`/`config-packages`/`delegations`/`email-templates`/`export-settings`/`kennzeichen-settings`/`license`/`object-types`/`query-console`/`retention-settings`/`share-link-settings`/`signature-config`/`storage-guard`/`storage-operational-config`/`teamspaces`/`upload-settings`) without ever correcting this doc. Phase 45 Session 5 audited every remaining page and gated the four that had a real, cleanly-scoped backend permission to gate on: `/archival-transfers/` (`archival.read`), `/forensic-trace/` (`reporting.forensic_trace`), `/reports/` (`reporting.read`), `/superuser/` (`breakglass.approve` — the capability that actually gates this page's core request/approve flow server-side, not `system.not_shutdown.trigger`, which only conditionally shows one narrower button already client-gated inside `SuperuserBreakGlass` itself). `AdminSidebar.tsx`'s nav entries gained matching `requiresCapability` values for the same four (defense-in-depth, same pattern as every other gated entry).
- **Deliberately left ungated: `/deletion-register/`, `/registry/`, `/installations/`, `/ocr-settings/`** (audited in Phase 45 Session 5) — the backend endpoints these pages call have no permission check *requiring* it (`/installations/` calls no backend at all, being pure `localStorage`; `document-service`'s `GET /deletion-register` and `registry-service`'s `GET /instances` genuinely have zero `Depends()`-based auth). **Corrected during Phase 65+'s gap-analysis round**: `/ocr-settings/` is not quite the same case as the other three — `ocr-service`'s `GET`/`PUT /config` DO call `permission_client.check()` for `ocr.read`/`ocr.write` (ADR 0073) and would `403` a denied caller, it's just that the "everyone" group grants both by default, an open-by-default POLICY rather than a missing check. Adding client-side gating with no server-side backing would still imply a permission boundary that doesn't actually exist today — left as-is rather than inventing one; a future backend-hardening session could tighten the "everyone" grant on `ocr.read`/`.write` first (for `/ocr-settings/`) or add real permission checks (for the other three), then gate the page. `/processing-failures/` **removed from this list in P63-S3**: it still mixes gated and ungated sources so the PAGE stays ungated, but one of its four sources (notifications) gained a real capability in Phase 59 Session 1 — closed via a per-section client-side gate instead of a page-level one, see "Processing Failure Visibility" below.
- ~~**Pre-existing, unrelated test failure found during Phase 45 Session 5's full test run**: `processing-failures.test.tsx`'s `"retries a failed handover (result leg) and reloads"` fails (`retryHandoverMock` never called after the button click) — reproduced in isolation, unrelated to this session's diff (no file this test depends on was touched), not investigated or fixed here since it falls outside this session's own scope (UI RBAC gating + case document titles). Worth a dedicated look in a future session.~~ — **fixed in P63-S3**: the test never filled in the operator-key input ADR 0162 (Phase 44 Session 1) made required for the retry button to even be clickable, and asserted a stale single-arg `retryHandover` call. Fixed by filling the field and asserting the real two-arg call. First noticed as a flake back in Phase 47 Session 4 and repeatedly re-confirmed-but-deferred across several later sessions (Phase 45 Session 5, Phase 52 Sessions 1/2, P53-S1/S3) — finally fixed here while getting the frontend regression suite green for this session's own (unrelated) UI change, not a dedicated session of its own.
- ~~No "dispose now" control (5.6, since P7-S3) — `ArchivalTransfersView` is pure observation/retrieval; a manual trigger would have to go through `document-service`'s `POST /documents/{id}/archive-request`, for which there is no Admin UI integration yet~~ — **fixed in Post-Roadmap Phase 22 Session 1** (see "Records Disposal & Long-Term Archiving" above): a new form calls the endpoint by document ID. Still **no** button directly on the document in the User UI (the Admin UI has no document list/search, only free-text ID entry) — a possible future extension.
- **Not-shutdown control (4.8, since P6-S6) is purely client-side for visibility, not enforcement** — `SuperuserBreakGlass` only hides the form/button, actual enforcement happens exclusively at the Permission Service; no new nav entry, since it is content-wise coupled to the existing break-glass page (4.8 itself references 4.6).
- `naming_constraints`/`conditions` still have no guided UI form (only retained unchanged when editing) — free-text/JSON editing of these two fields is not part of P5b-S3 and remains an open point for a later session.
- ~~**No admin-UI form support for `reference_target`** ([ADR 0193](../adr/0193-cross-service-reference-validation.md) "Consequences") — `ObjectTypeEditor.tsx` offers `"reference"` as an attribute type but does not expose a target picker; setting `reference_target` required direct API access.~~ — **closed in P71-S3**: a `<select>` (unset / document / folder) shown only for `type: "reference"` attributes, wired into `toBackendAttribute`/the edit-load path. Note this is a fixed 2-value service-target selector (which of the two instance-holding services, `document` or `folder`, the value is checked against), NOT a picker over other object types — `reference_target` never pointed at another object type, see the ADR's own "Rationale" for why the scope was deliberately narrower than that.
- ~~No retroactive-impact check when an attribute is renamed/removed during editing while it is already referenced in a saved layout override (same limitation as ADR 0014) — the object type editor deliberately does not touch existing layouts, the next time the affected layout is opened in the layout designer would show the orphaned reference.~~ — **closed in Phase 79 Session 1**: `LayoutDesigner` (not `ObjectTypeEditor` itself — the actual home of the fix, since that's where a saved layout is loaded/displayed, `ObjectTypeEditor` never touches layouts) now computes the difference between a CUSTOM layout's referenced attribute names and the object type's current attribute list on every load and shows a warning banner naming the stale attribute(s) if any — a GENERATED layout (`is_custom: false`) is always freshly computed from the current attributes and can never go stale, so it's never checked. Still no automated fix (remove/replace stays a manual follow-up action, same "consistency checking only on the respective write operation, no background reconciliation" philosophy as ADR 0013).
- Layout designer does not support multi-column rows via drag and drop, only the unambiguous row/field operations described above (deliberate design decision without browser verification, see above).
- Icon selection is a curated, frontend-hardcoded set of seven icons (no upload) — Concept 13's open point on icon format/origin is thus pragmatically, but not conclusively, answered for this session; switching to a larger or customizable icon set would remain backward-compatible, since the backend only stores a free string.
- ~~No group management, only individual users (Permission Service already supports `principal_type=group`, the UI only offers `user`).~~ — **reworded during Phase 65+'s gap-analysis round**: group ENTITY management (create/delete groups, add/remove members) has existed since Post-Roadmap Phase 22 Session 2 — the blanket "no group management" claim was stale. The narrower gap remains real: `UserManagement.tsx`'s `createRoleAssignment` hardcodes `principalType: "user"`, so a ROLE ASSIGNMENT can still only ever target a `user` principal, never a `group`, even though Groups are now real, manageable entities. — **closed in Phase 78 Session 3**: the role-assignment form now has a "Zuweisen an" (assign-to) select (`user`/`group`); picking `group` swaps the free-text username field for a `<select>` populated from the already-loaded `groups` list, sending the chosen `Group.id` as `principal_id` with `principalType: "group"` — `permission-service`'s `POST /role-assignments` already accepted `principal_type: "group"` server-side, this was purely a frontend gap. The assignments table also gained a "Typ" column and resolves a group principal's name via the same `groups` list instead of showing a raw id.
- ~~Workflow designer, license overview, audit trail view, configuration import/export (Concept 8 names these for the Admin UI) are not part of this base scaffold — the underlying services do not yet exist.~~ — **closed in Phase 57 Session 1**: license overview (`app/license/page.tsx`), audit trail view (`app/audit-trace-settings/page.tsx`), and configuration import/export (`components/ConfigPackages.tsx`) all shipped in later sessions and are stale here. Workflow designer remains correctly out of scope for admin-ui specifically — it is a separate app, `process-designer` (Phase 24), not an embedded admin-ui page.
- ~~i18n only structurally prepared (ADR 0007), no second language and no UI language switch.~~ — **closed in Phase 47 Session 4**: full English translation (`en.json`, 754 keys) plus `LocaleSwitcher` in `AdminShell.tsx`'s header, see "i18n: English Translation + Locale Switcher" above. Stale bullet, left un-struck when that session shipped — corrected during a later gap-analysis round.
- ~~**Installation list is stored purely locally in the browser, no cross-device provisioning** (see ADR 0008 "Consequences"). The "optional, not-yet-built Fleet/License Management Service" this bullet blamed is stale — `fleet-management-service` has fully existed since Phase 54 Session 1 (full CRUD: `POST`/`GET /installations`, `/installations/{id}/license`, group/rollout management), it was simply never wired into `admin-ui`.~~ — **closed in P67-S1** ([ADR 0197](../adr/0197-admin-ui-fleet-management-view.md)): a new, separate `FleetManagementView` (`/fleet-management/`) now wires up registration/listing/deletion/status/license-push against the real service. Deliberately NOT a change to `InstallationManager.tsx`/`installations.ts` — those manage an unrelated concept (which gateway THIS browser's admin session logs into, Concept 8), not the operator's fleet inventory; conflating the two would have merged two data models that don't correspond field-for-field. ~~Groups/plans/rollouts (the rest of `fleet-management-service`'s API) remain unbuilt in `admin-ui`~~ — built in P88-S1, see "Fleet Orchestration".
- Theme preference has no conflict resolution mechanism between devices/installations (last fetch wins) and no retry on a failed `PUT /me/preferences` (see ADR 0009 "Consequences").
- `ocrEnabled` is not editable on the new OCR settings page, only indirectly visible (reachable/unreachable) — actual on/off switching remains a deployment action (Compose profile), see ADR 0016.
- The Storage Service's target set (which backends/credentials are configured) is likewise not editable on the storage guard page, only the admin override — target-set changes remain deployment configuration (`DMS_TARGETS`), see ADR 0017.
