# office-addin

**Responsibility:** Microsoft Office add-in (Office.js, **Word** only) for native OG Doc integration (3.3a): opening/saving a document directly from/to OG Doc, inline metadata editing, workflow start/continuation, a central role-based template library — each "without having to open the DMS interface separately" (concept wording). P14-S8; the equivalent LibreOffice/OpenOffice extension (UNO `.oxt`) is P14-S9, a separate session.

**Concept reference:** 3.3a, 3.3, 7.1, 2.2, 2.5
**No own Postgres schema** — a purely client-side rendered SPA (static export, same pattern as `apps/user-ui`, ADR 0006), no own backend process. Talks to **not a single new endpoint** — full reuse of `document-service`/`workflow-service`/`object-type-service`/`folder-service`/`search-service`/`auth-service`.
**ADR:** [0045 — Word only, full endpoint reuse, document linking via `document.settings`](../adr/0045-office-addin-word-only-reused-endpoints-settings-linking.md)

## Location in the Repo

`apps/office-addin/` — Next.js static export + `manifest.xml` (Office add-in manifest, XML format, more broadly compatible than the newer JSON manifest) + `assets/` (icons). `manifest.xml`/`assets/` are **not** a Next.js build output — they are copied separately into the Docker image (see `Dockerfile`).

## Feature Scope

| Area | Implementation |
|---|---|
| **Open from OG Doc** | `DocumentPicker` (full-text search via `search-service`, 3.7/3.7a) → load content (`GET /documents/{id}/content`) → `Word.run(... insertFileFromBase64(base64, InsertLocation.replace))` replaces the entire Word document content → attempt lock (`POST /documents/{id}/lock`) → persist the link in `Office.context.document.settings`. |
| **New from template** | `TemplatePicker` lists documents from the root folder "Templates" (name configurable, see below) → load content and insert into the currently empty Word document (identical `insertFileFromBase64` path) → on first save, `POST /documents` with `derived_from_document_id`/`derived_from_version_number` = the template. |
| **Save to OG Doc** | `Office.context.document.getFileAsync(Compressed, ...)` reads the current raw Word bytes → `POST /documents/{id}/versions` (check-in, `expected_base_version_number` = last known version — optimistic conflict detection like every other check-in client; on a version mismatch, a conflict copy is created instead of an error). |
| **Inline metadata** | `MetadataForm` (title + one text field per object type attribute, `GET /object-types/{id}` for the schema) → `PATCH /documents/{id}`. |
| **Start/continue workflow** | `WorkflowPanel`: `GET /instances?business_key={documentId}` + `GET /instances/{id}/tasks` for running instances of this document, `POST /instances/{id}/tasks/{id}/complete` to complete a task, `POST /process-definitions/{id}/instances` with `business_key={documentId}` to start a new workflow. |

## Document Linking: `Office.context.document.settings` Instead of Server State

Which OG Doc document (ID + last known version number) belongs to the currently open Word file is stored via `Office.context.document.settings` — add-in-owned state that ends up **in the file itself** (its own custom XML part). After closing and reopening the same file (with the add-in enabled), the link is automatically present again, without a backend needing to maintain a file-to-document mapping. See `src/lib/office.ts` (`getLinkedDocument`/`setLinkedDocument`/`clearLinkedDocument`).

## Locking Instead of Only Optimistic Conflict Checking

Unlike `user-ui` (which deliberately only uses the optimistic version check on check-in, ADR 0002), this add-in uses the already existing explicit lock (`POST`/`DELETE /documents/{id}/lock`), previously unused by any frontend — a Word editing session can take a long time, and a "someone else is currently editing this" notice BEFORE editing begins makes more sense here than only a conflict on save. If the lock fails (`409`, someone else already holds it), the document is still opened read-only (title/metadata/workflow remain readable), but the "Save to OG Doc" button is disabled (`document-service`'s `checkin_version` would reject the write attempt server-side anyway).

## Template Library (3.3a) — Naming Convention Instead of a New Mechanism

A template is an **ordinary document** in the root folder `Templates` (name configurable via `NEXT_PUBLIC_TEMPLATE_LIBRARY_FOLDER_NAME`/`OFFICE_ADDIN_TEMPLATE_LIBRARY_FOLDER_NAME`) — "role-based" (concept wording) is thereby automatically the already existing folder read-permission check (`permission-service`), no new permission logic, no new endpoint. An admin creates the folder manually and grants read rights like for any other folder. **Not** the same concept as the future structural "templates" (2.5/P15-S6, file-plan scaffolding via the JSON structure export) — see ADR 0045 for the distinction.

## Backend Integration

Exclusively via the API gateway (3.5), no direct backend addresses:

| Action | Gateway call |
|---|---|
| Log in / identity | `POST /api/auth-service/login`, `GET /api/auth-service/me` |
| Search (document picker) | `GET /api/search-service/search?q=` |
| Read document / content | `GET /api/document-service/documents/{id}`, `GET /api/document-service/documents/{id}/content` |
| Check in new version | `POST /api/document-service/documents/{id}/versions` |
| Create new document (from template) | `POST /api/document-service/documents` |
| Change metadata | `PATCH /api/document-service/documents/{id}` |
| Lock/unlock | `POST`/`DELETE /api/document-service/documents/{id}/lock` |
| Root folder/template list | `GET /api/folder-service/folders/root/children`, `GET /api/document-service/documents?folder_id=` |
| Object type schema | `GET /api/object-type-service/object-types/{id}` |
| Workflow | `GET /api/workflow-service/process-definitions`, `GET /api/workflow-service/instances?business_key=`, `GET /api/workflow-service/instances/{id}/tasks`, `POST /api/workflow-service/instances/{id}/tasks/{id}/complete`, `POST /api/workflow-service/process-definitions/{id}/instances` |

## Auth

Identical pattern to `reviewer-ui`/`migration-console`: a simple login form (`POST /login`), tokens in `localStorage` (`ogdoc.tokens`, ADR 0006). A special nuance in the Office task-pane context: the storage location/lifetime of the task-pane webview differs by Office version/platform — having to log in again after a Word restart is an expected case, not a bug (see "Open Points").

## Manifest & Deployment

`manifest.xml` (XML format, `TaskPaneApp`, host `Document` = Word) declares a single ribbon button on the Home tab that exclusively opens the task pane (`ShowTaskpane` — no separate `FunctionFile` logic needed, every interaction happens within the task pane). Verified with the official `office-addin-manifest validate` tool (Microsoft) — "The manifest is valid.", runnable on Word 2013+/Windows/Mac/Web per the manifest structure.

**HTTPS requirement**: Office only loads add-in web content over HTTPS (apart from a few documented local exceptions). This stack runs consistently over HTTP in the development environment, like every other service — `office-addin` would need its own TLS termination point for a real sideload test (see README.md "Local Sideload Testing").

## Tests

- `npm run typecheck`/`npm run lint`/`npm run build` — clean.
- `npm test` (Vitest): **18 tests**.
  - `tests/office-lib.test.ts` (8): `lib/office.ts` against a handwritten `Office`/`Word` fake (`tests/office-mock.ts`) — setting/reading/clearing the link including `saveAsync`, `insertFileFromBase64` receives the expected base64 content, file slices are correctly assembled into a base64 string, `base64ToBlob`/`blobToBase64` round trip.
  - `tests/auth-context.test.tsx` (4): identical login/logout/session-restoration pattern as the other apps, its own storage key (`ogdoc.tokens`).
  - `tests/task-pane.test.tsx` (6): empty state shows the document/template picker; opening a document loads it via `Word.run` into the document and links it; a lock conflict (`409`) shows a read-only notice and disables saving; "New from template" creates a new document on first save with correct `derivedFromDocumentId`/`derivedFromVersionNumber`; saving sends the expected `expected_base_version_number` and updates the linked version; unlinking releases the lock and returns to the picker view.
- **`npx office-addin-manifest validate manifest.xml`** (a real, official Microsoft tool) — "The manifest is valid.", no warnings.
- **Live against the real running stack** (curl, no real Office host available): all reused backend endpoints individually verified — see "Open Points" for the verification deliberately not possible here.

## Open Points

- **No verification against a real Office host possible** — no Windows/Office/valid Microsoft 365 sideloading tenant in this development environment, no headless/containerized way to actually run Word (unlike the ephemeral Playwright approach for browser UIs). A human should actually sideload the add-in into Word and click through it before production use.
- **Word only** — Excel/PowerPoint/Outlook remain completely untouched (no comparable "replace the entire document" JS API available, see ADR 0045).
- **No theme switcher/maintenance banner** — deliberately omitted (space constraints; an add-in should ideally follow Office's own theme rather than have its own toggle).
- **`MetadataForm` is a simple one-text-field-per-attribute form** — no type-specific widgets/layout arrangement like `user-ui`'s `LayoutFormFields` (2.2b), appropriate for the narrow task-pane width.
- **Template library requires manual admin setup** (create the "Templates" folder, grant read rights) — no automated bootstrap, no Admin UI component for it.
- **No deleting documents/folders, no retention/legal-hold access** from the task pane — deliberately limited to the 3.3a feature scope.
- **HTTPS termination for a real sideload test not part of this session** (see above).
- **Tokens in `localStorage`** (ADR 0006) — with an additional, platform-dependent nuance in the task-pane webview context (webview lifetime varies by Office version), see "Auth" above.
