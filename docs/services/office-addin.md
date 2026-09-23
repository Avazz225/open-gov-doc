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

## Theming

Reduced variant of the cross-app theming pattern (8, ADR 0009) — automatic light/dark only via `prefers-color-scheme`, no manual switcher, no `data-theme` blocks (`Shell.tsx`: the task pane should ideally follow Office's own theme rather than have its own toggle). **Since Phase 49 Session 1** ([ADR 0168](../adr/0168-shared-design-tokens-and-scales.md)): `globals.css`'s own `--dms-*` color-token declarations were removed and replaced by `@import "../../../../libs/dms-ui/tokens.css";` — this app was previously missing several tokens entirely (`--dms-accent-bg-strong`, `--dms-surface`, `--dms-surface-fg`, and `--dms-success-bg`), confirmed fixed via a computed-style check (`getComputedStyle(document.documentElement).getPropertyValue("--dms-success-bg")` now resolves). The reduced theming itself (no `data-theme`, no switcher) is unchanged — adopting the shared file only fixes the token gaps, per ADR 0168's explicit "stays as-is" decision for this app. Hardcoded spacing/radius/font-size values elsewhere in `globals.css` were also switched to the new scale tokens where they matched a step; `font-size: 14px` on `:root` (a deliberate, narrower base size for this app's narrow task pane) stays a literal value, independent of the shared typography scale.

**Since Post-Roadmap Phase 75 Session 1** ([ADR 0218](../adr/0218-tailwind-css-v4-tooling-foundation.md)): `tailwindcss`/`@tailwindcss/postcss`/`postcss` added as devDependencies, a new `postcss.config.mjs`, and `libs/dms-ui/tailwind-preset.css` imported into `globals.css` (mapping Tailwind utility classes onto the `--dms-*` tokens above) — tooling only, this app's own reduced theming is unchanged (preflight deliberately omitted for now, see the ADR); this app's own eventual rollout should stay consistent with its already-established "reduced theming, follows host app" exception (Phase 48/49), not force-unify it with the other five apps.

**Since P69-S2** ([ADR 0201](../adr/0201-p69s1-branding-and-role-dependent-dashboard-scoping.md)): the new installation-level branding config (product name/accent color/logo, `registry-service`'s `GET /installation/branding`) is deliberately NOT wired into this app — the same "follow the host" reasoning applies a third time (after theming/ADR 0168 and locale/ADR 0167): this add-in is a task pane embedded inside Word, not a standalone product with its own login screen or landing page a per-installation brand identity would meaningfully attach to (no `login.heading`-equivalent single touchpoint exists here at all — "OG Doc" appears scattered across many small utility strings like "Open from OG Doc"/"Save to OG Doc" instead of one clean render site). `user-ui`/`admin-ui`/`reviewer-ui`/`process-designer`/`migration-console` all got the branding provider this session; `libreoffice-addin` was never in scope for it either (no browser DOM, see that app's own docs).

## i18n: Follows the Host Locale, No In-App Switcher

**Since Phase 47 Session 2** ([ADR 0167](../adr/0167-locale-switcher-pattern-and-office-addin-host-locale.md), decision recorded in P47-S1, built here): own `src/i18n/en.json` added, but unlike `process-designer`/`reviewer-ui`/`migration-console`, this app gets no `LocaleSwitcher` — it detects and follows Word's own display language automatically instead, the same "follow the host" reasoning already applied to the missing theme switcher (see "Open Points" below). `I18nProvider`'s previously-static `locale` prop is now driven by a new `LocaleProvider`/`useLocale()` (`lib/locale-context.tsx`), started at `defaultLocale` ("de", matching this static export's server-rendered markup); `OfficeGate` (`components/OfficeGate.tsx`) calls `setLocale` exactly once, right after `Office.onReady()` resolves, based on `Office.context.displayLanguage` (a BCP-47 tag like `"en-US"`/`"de-DE"` — only the primary language subtag is matched, anything without a matching dictionary falls back to `defaultLocale`, see `resolveLocaleFromDisplayLanguage`). `Office.context.displayLanguage` is not valid before `Office.onReady()`, the same constraint every other `Office.context` access in `lib/office.ts` already respects — detection could not simply run in `LocaleProvider`'s own mount effect the way `process-designer`'s cached-locale restore does.

## Manifest & Deployment

`manifest.xml` (XML format, `TaskPaneApp`, host `Document` = Word) declares a single ribbon button on the Home tab that exclusively opens the task pane (`ShowTaskpane` — no separate `FunctionFile` logic needed, every interaction happens within the task pane). Verified with the official `office-addin-manifest validate` tool (Microsoft) — "The manifest is valid.", runnable on Word 2013+/Windows/Mac/Web per the manifest structure.

**HTTPS requirement**: Office only loads add-in web content over HTTPS (apart from a few documented local exceptions). This stack runs consistently over HTTP in the development environment, like every other service — `office-addin` would need its own TLS termination point for a real sideload test (see README.md "Local Sideload Testing").

**Since Phase 49 Session 1**: the build stage's `WORKDIR`/`COPY` layout changed to mirror the actual repo shape (`/repo/apps/office-addin/`, plus `/repo/libs/dms-ui/`) instead of flattening this app into `/app` — needed so `globals.css`'s new relative `@import` of `libs/dms-ui/tokens.css` resolves identically to a local `npm run build`. The `manifest.xml`/`assets/` copy into the final `nginx` stage is unaffected (already sourced from the build context, not the build stage's output).

## Tests

- `npm run typecheck`/`npm run lint`/`npm run build` — clean.
- `npm test` (Vitest): **21 tests since Phase 47 Session 2**, previously 18.
  - `tests/office-lib.test.ts` (8): `lib/office.ts` against a handwritten `Office`/`Word` fake (`tests/office-mock.ts`) — setting/reading/clearing the link including `saveAsync`, `insertFileFromBase64` receives the expected base64 content, file slices are correctly assembled into a base64 string, `base64ToBlob`/`blobToBase64` round trip.
  - `tests/auth-context.test.tsx` (4): identical login/logout/session-restoration pattern as the other apps, its own storage key (`ogdoc.tokens`).
  - `tests/task-pane.test.tsx` (6): empty state shows the document/template picker; opening a document loads it via `Word.run` into the document and links it; a lock conflict (`409`) shows a read-only notice and disables saving; "New from template" creates a new document on first save with correct `derivedFromDocumentId`/`derivedFromVersionNumber`; saving sends the expected `expected_base_version_number` and updates the linked version; unlinking releases the lock and returns to the picker view.
  - `tests/office-gate.test.tsx` (3, new): host-locale detection via `resolveLocaleFromDisplayLanguage` — Word reporting `"de-DE"` keeps the default locale, `"en-US"` switches to English (both the `LocaleProvider` context value and `I18nProvider`'s actual dictionary), and an unsupported `"fr-FR"` falls back to the default rather than rendering an empty dictionary. `tests/office-mock.ts` gained a `displayLanguage` option (default `"de-DE"`) on `installOfficeMock()` for this.
- **`npx office-addin-manifest validate manifest.xml`** (a real, official Microsoft tool) — "The manifest is valid.", no warnings.
- **Live against the real running stack** (curl, no real Office host available): all reused backend endpoints individually verified — see "Open Points" for the verification deliberately not possible here.
- **Phase 49 Session 1's token-rollout check**: a real browser load of this app in this sandbox now reaches the point of actually fetching Microsoft's real, CDN-hosted `office.js` (confirmed via its own "Office.js is loaded outside of Office client" console warning) and then crashes inside Next.js's router (`window.history.replaceState is not a function`) — office.js patches browser APIs assuming a real Office host's message bridge, and has none here. Confirmed this is unrelated to the CSS change (not something this session introduced) by blocking the script via `page.route()`: with it blocked, the app renders its own graceful `OfficeGate` error state correctly, styled with the new shared tokens. Verified the actual custom-property values resolve correctly via `getComputedStyle(document.documentElement)` (including the two tokens this app was previously missing entirely, `--dms-success-bg`/`--dms-surface`/`--dms-surface-fg`) despite not being able to click through past `OfficeGate` in a real browser here — same root-cause limitation as every other "no real Office host" gap already listed below.

## Open Points

- **No verification against a real Office host possible** — no Windows/Office/valid Microsoft 365 sideloading tenant in this development environment, no headless/containerized way to actually run Word (unlike the ephemeral Playwright approach for browser UIs). A human should actually sideload the add-in into Word and click through it before production use.
- **Word only** — Excel/PowerPoint/Outlook remain completely untouched (no comparable "replace the entire document" JS API available, see ADR 0045).
- **No theme switcher/maintenance banner** — deliberately omitted (space constraints; an add-in should ideally follow Office's own theme rather than have its own toggle).
- **No in-app locale switcher, by decision** (decided in Phase 47 Session 1, built in Session 2, [ADR 0167](../adr/0167-locale-switcher-pattern-and-office-addin-host-locale.md)): this add-in follows the host Word application's own locale automatically instead of gaining its own switcher, same "follow the host" reasoning as the theme point above, if anything a cleaner fit here since a document editor showing UI text in a different language than the surrounding Word chrome would be actively confusing rather than a minor inconsistency. Host-locale detection cannot itself be verified against a real Office host, same limitation as the bullet above — covered instead via `tests/office-gate.test.tsx`'s mocked `Office.context.displayLanguage`, see "Tests".
- **`MetadataForm` is a simple one-text-field-per-attribute form** — no type-specific widgets/layout arrangement like `user-ui`'s `LayoutFormFields` (2.2b), appropriate for the narrow task-pane width.
- **Template library requires manual admin setup** (create the "Templates" folder, grant read rights) — no automated bootstrap, no Admin UI component for it.
- **No deleting documents/folders, no retention/legal-hold access** from the task pane — deliberately limited to the 3.3a feature scope.
- **HTTPS termination for a real sideload test not part of this session** (see above).
- **Tokens in `localStorage`** (ADR 0006) — with an additional, platform-dependent nuance in the task-pane webview context (webview lifetime varies by Office version), see "Auth" above.
