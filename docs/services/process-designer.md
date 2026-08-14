# process-designer

**Responsibility:** Standalone frontend application for graphical BPMN 2.0 modeling against the Workflow Engine (`workflow-service`, P6-S1) — create/open/edit process definitions, import/export BPMN XML, configure the Signature Task (3.10) via its own properties panel, process versioning (7.1/8, P6-S8), since **P6-S9** configure federated process steps (7.4) via its own properties panel, since **P14-S4** create/edit DMN 1.3 decision tables (`dmn-js`) and the Business Rule Task `decisionRef` (existing properties panel module).
**Concept Reference:** 7.1, 8, 3.10, 7.4
**No own Postgres schema** — purely client-side rendered SPA (static export, same pattern as `apps/user-ui`, see [ADR 0006](../adr/0006-user-ui-static-export-spa.md)), no dedicated backend process.

## Location in the Repo

`apps/process-designer/` — deliberately **not** under `services/` (Node/React toolchain instead of the Python service template, ADR 0006) and deliberately **not** part of the Admin UI (literal concept requirement 7.1/8: "standalone frontend application").

## Single Installation (deliberately not multi-installation like Admin UI)

Unlike `apps/admin-ui` (`InstallationProvider`, multi-installation, ADR 0008), Process Designer follows the simpler `apps/user-ui` pattern: a single gateway address baked in at build time (`NEXT_PUBLIC_GATEWAY_BASE_URL`), global `dms.tokens` storage key. **Explicit user decision at plan approval time**: Process Designer remains non-multi-installation even after cross-installation functionality was requested (external swimlanes/handover, see below) — anyone who wants to use the UI runs the container in their own installation; switching between multiple installations (as in the Admin UI) is not supported.

## Pages

| Route | Purpose |
|---|---|
| `/login/` | Login (identical to user-ui/admin-ui) |
| `/` | Tab switcher between `ProcessDefinitionList` and `DmnDefinitionList` (since P14-S4) — reachable only with a valid session |
| `/designer/` | BPMN canvas + properties panel, optional `?id=` query parameter (no dynamic Next.js route parameter, same SPA state pattern as the existing apps) |
| `/dmn-designer/` | **Since P14-S4**: `dmn-js` decision table/DRD editor, same `?id=` pattern as `/designer/` |

`RequireAuth` renders a `MaintenanceBanner` (emergency shutdown, 4.8) first, as in user-ui/admin-ui.

## Authorization (button-level, not route-level)

**Reading/opening/canvas display remains open to every authenticated principal** (`GET /process-definitions*` is ungated on `workflow-service`). Only **saving/deleting** are tied to the `admin.object_config` capability that has existed since P6-S6 — checked via `permissions.includes("admin.object_config")` directly in `ProcessDefinitionList`/`designer/page.tsx` (button hidden/disabled, no route-wide `RequireCapability` redirect as in the Admin UI, since reading should remain permitted). The backend endpoint itself enforces the same rule regardless (`403`) — the UI gating is purely a UX anticipation.

## Process Versioning (since P6-S8, [ADR 0027](../adr/0027-workflow-process-definition-versioning.md))

`name` is the process family key on the backend, no longer globally unique. `ProcessDefinitionList` shows only the **newest version per family** by default (`GET /process-definitions`); an expandable version history per row lazily loads `GET /process-definitions?name=X` (full history, newest first). "Save" in the designer has no separate "version vs. new family" toggle: the name field decides — leaving it unchanged saves a new version of the same family, a changed name creates a new family (version 1). After saving, the page navigates to the newly created `id` (`router.replace`); the success message states the assigned version number.

**Since Post-Roadmap Phase 21 Session 4** ([ADR 0087](../adr/0087-bpmn-import-review-gate.md)): `workflow-service` can optionally gate `workflow.process_definition.import` behind the four-eyes principle. In that case `createProcessDefinition()` returns `{status: "pending_approval", approval_request_id}` instead of the usual `ProcessDefinitionSummary` (`lib/api.ts`'s `isPendingApproval()` type guard distinguishes the two shapes). `handleSave()` in `designer/page.tsx` then does **not** navigate to the new `id` (it doesn't exist yet) and instead shows the notice text `designer.savePendingApproval` — the process definition is only created asynchronously after approval by a second admin. No dedicated test for this branch (see "Tests" below).

## BPMN Canvas (`components/BpmnDesigner.tsx`)

`bpmn-js` (**without** `bpmn-js-spiffworkflow`, see [ADR 0026](../adr/0026-process-designer-bpmn-js-without-spiffworkflow-addon.md)) + `bpmn-js-properties-panel` + `@bpmn-io/properties-panel` + `camunda-bpmn-moddle` (for the `camunda:` extension elements read by `workflow-service`'s `CamundaParser`, P6-S7). Manual `useRef`/`useEffect` mounting instead of a React wrapper (no actively maintained React wrapper for bpmn-js is available), loaded via `next/dynamic`/`{ssr:false}` in `designer/page.tsx` (bpmn-js manipulates the DOM directly — incompatible with Next.js' build-time render pass under `output:"export"`). The modeler exposes `exportXml`/`importXml` via an `onReady(handle)` callback prop instead of `forwardRef`/`useImperativeHandle` — avoids any uncertainty about ref forwarding through `next/dynamic`.

Standard task types (Manual/Script Task) use bpmn-js' built-in default palette/context pad behavior, no custom palette entry.

## Signature Task Properties Panel (3.10, `components/SignatureTaskPropertiesProvider.tsx`)

The Signature Task is **not its own BPMN element**, but a `bpmn:ManualTask` with an additional, dedicated properties panel group (visible only for Manual Tasks): a "Signature required" checkbox + level selector (SES/AES/QES), reading/writing `bpmn:extensionElements/camunda:properties` (`taskType=signature`, `requiredLevel=...`) — exactly the format that `workflow-service`'s `CamundaParser` has recognized as a Signature Task since P6-S7. The provider/group/entry registration pattern (`propertiesPanel.registerProvider`, `bpmnFactory.create`, `commandStack.execute('element.updateModdleProperties', ...)`) was verified against the actually installed, bundled source of `bpmn-js-properties-panel`, not assumed (see ADR 0026). `useService` is re-exported by `bpmn-js-properties-panel`, not by `@bpmn-io/properties-panel` — noticed and fixed as an import error during the first production build.

## Federated Step Properties Panel (7.4, `components/FederatedStepPropertiesProvider.tsx`, since P6-S9)

Same basic pattern as the Signature Task: **not its own BPMN element**, but an additional properties panel group "Federation (7.4)" on `bpmn:ManualTask` elements — a "Federated step" checkbox, a "Target installation" dropdown, a "Target process type" text field, reading/writing `camunda:properties` (`taskType=federated`, `targetInstallationId`, `targetProcessType`) — exactly the format that `workflow-service`'s `_dispatch_pending_federation_tasks` has recognized as a federated step since P6-S9 (see `docs/services/workflow-service.md` "Federation"). Deliberately duplicated rather than shared helper module with `SignatureTaskPropertiesProvider.tsx` — two independent, small providers are easier to follow than a prematurely shared abstraction for two use cases.

The installation list for the dropdown is loaded **once, before the modeler is created** (`designer/page.tsx`, `listFederationInstallations()`) and injected as a static didi value into `additionalModules` (`{ federationInstallations: ["value", ...] }`) — no live reloading during an editing session. **The entire group stays hidden if the list is empty** (no hub configured or no installations known in the address book) — a literal fulfillment of concept 7.1: "the Process Designer does not even offer federated process steps as an option in the first place." Deliberately **no real swimlane editing** in bpmn-js (no established provider pattern for it, significantly higher risk) — the roadmap phrase "external swimlanes" is meant purely as UX framing here; technically the goal remains a property of the individual process step, not of the lane.

## DMN 1.3 Decision Tables (7.1, since P14-S4)

**Business Rule Task `decisionRef`: no new properties panel code needed.** Empirically verified before implementation via a real Playwright browser test (not merely taken from `bpmn-js-properties-panel` documentation): the already-registered `CamundaPlatformPropertiesProviderModule` (in use since P6-S7/P6-S9 for Signature/Federation tasks, `BpmnDesigner.tsx`) already renders a complete "Implementation" group (`Type: DMN`, `Decision reference`, `Binding`, `Tenant ID`, `Result variable`) for a selected `bpmn:businessRuleTask` — an imported Business Rule Task with `camunda:decisionRef="approval-level"` showed the correctly pre-populated field without any additional provider registration.

**`components/DmnDesigner.tsx`** — a standalone editor for the decision tables themselves (not the BPMN reference to them): `dmn-js` (bpmn.io toolkit, [ADR 0021](../adr/0021-bpmn-io-license-watermark.md) addendum), identical manual `useRef`/`useEffect` mounting + `next/dynamic`/`{ssr:false}` loading + `onReady(handle)` callback pattern as `BpmnDesigner.tsx`. Compatibility with the pinned `bpmn-js` 18.22.1 stack was empirically confirmed via a spike: `dmn-js` 17.10.1 uses the same `diagram-js` major version (`^15.23.2`); both a real `next build`/static export run and a live browser test (decision table view including hit policy dropdown, rule rows, import/export) completed without errors — no fallback to a raw XML editor was needed. Like `bpmn-js-properties-panel`, `dmn-js/lib/Modeler` ships no own TypeScript declarations (`src/types/untyped-modules.d.ts`).

**`components/DmnDefinitionList.tsx`/`app/dmn-designer/page.tsx`** — an exact analog of `ProcessDefinitionList.tsx`/`designer/page.tsx` (same versioning pattern: `name` is the family key, plus a `decision_id` column alongside the version history). Since P14-S4, `app/page.tsx` switches between `ProcessDefinitionList` and `DmnDefinitionList` via a simple tab switcher (`.tab-bar`) instead of maintaining two separate home page routes.

**Real bug found and fixed (P14-S4)**: in `designer/page.tsx`, `loadedForId = useRef<string | null>(null)` was compared against `id = searchParams.get("id")` (also `null` without `?id=`) — `null !== null` is `false`, so the create-new branch (`setInitialXml(STARTER_BPMN_XML)`) never ran on the very first call to `/designer/` WITHOUT `?id=`, leaving the designer permanently blank ("no diagram to display"). Discovered via a real browser test (not visible via Vitest/jsdom, since no real navigation without a query parameter occurs there) — fixed by using an `undefined` sentinel instead of `null` (`useRef<string | null | undefined>(undefined)`), the same fix was applied to `dmn-designer/page.tsx` from the start. Affected creating any new process without a prior `id`, not specific to DMN.

## Backend Integration

Exclusively via the API Gateway (3.5), no direct calls:

| Action | Gateway Call |
|---|---|
| Login | `POST /api/auth-service/login` |
| Identity after login | `GET /api/auth-service/me` |
| Effective capabilities (4.6) | `GET /api/permission-service/effective-permissions/{sub}/root` |
| Newest version per process family | `GET /api/workflow-service/process-definitions` |
| Full version history of a family | `GET /api/workflow-service/process-definitions?name=X` |
| Single process definition incl. BPMN XML | `GET /api/workflow-service/process-definitions/{id}` |
| Save (new version or new family) | `POST /api/workflow-service/process-definitions` (multipart, `admin.object_config`) |
| Delete a version | `DELETE /api/workflow-service/process-definitions/{id}` (`admin.object_config`, `409` on active instances) |
| Read/write theme preference | `GET/PUT /api/auth-service/me/preferences` |
| Emergency shutdown / maintenance mode status | `GET /api/permission-service/maintenance-mode` |
| Federation hub address book (since P6-S9) | `GET /api/workflow-service/federation/installations` (proxy, ungated, empty without a configured hub) |
| Newest version per DMN family (since P14-S4) | `GET /api/workflow-service/dmn-definitions` |
| Full version history of a DMN family (since P14-S4) | `GET /api/workflow-service/dmn-definitions?name=X` |
| Single DMN definition incl. `dmn_xml` (since P14-S4) | `GET /api/workflow-service/dmn-definitions/{id}` |
| Save DMN (since P14-S4) | `POST /api/workflow-service/dmn-definitions` (multipart, `admin.object_config`) |
| Delete DMN version (since P14-S4) | `DELETE /api/workflow-service/dmn-definitions/{id}` (`admin.object_config`) |

## Theming/i18n/Auth State

Identical copy of the providers from user-ui/admin-ui (`ThemeProvider`, `I18nProvider`, `auth-context.tsx`), own `src/i18n/de.json`. `auth-context.tsx` is a hybrid variant: global `dms.tokens` key like user-ui, but additionally a `permissions: string[]` field (`getEffectivePermissions`) like admin-ui, for the capability gating described above.

## Build & Deployment

Two-stage Docker image (`apps/process-designer/Dockerfile`, `node:22-alpine` build stage → `nginx:alpine` runtime), `NEXT_PUBLIC_GATEWAY_BASE_URL` as a build arg, overridable via `PROCESS_DESIGNER_GATEWAY_BASE_URL` in `infra/.env`. `infra/docker-compose.yml`: port `${PROCESS_DESIGNER_PORT:-3002}:80`.

## Tests

- `npm run typecheck` / `npm run lint` / `npm run build` — TypeScript check, ESLint (including one deliberate, justified `no-explicit-any` exception in `SignatureTaskPropertiesProvider.tsx`: `bpmn-js` itself types `Moddle`/`ModdleElement` as `any`, and neither properties panel package ships type declarations at all), production-ready static export.
- `npm test` (Vitest + Testing Library, **39 tests since P14-S4**, previously 28): `AuthProvider` (login/logout/session restoration incl. `permissions`), `ThemeProvider` (default/cache/persistence, copy of the user-ui pattern), `ProcessDefinitionList` (shows only the newest version, expandable/collapsible version history, deletion only with `admin.object_config` incl. notice text without it, backend error message on `409`), `BpmnDesigner` (with mocked `bpmn-js`: since P6-S9 the modeler is instantiated with six instead of four expected modules, `initialXml` is imported, `onReady`/`onImportError` callbacks, destroyed on unmount), `SignatureTaskPropertiesProvider` (pure `getSignatureLevel`/`isSignatureRequired` read functions against a minimal moddle element double, without a real `bpmn-moddle` model or DOM), since **P6-S9** `FederatedStepPropertiesProvider` (analogous pure read functions), since **P14-S4**: `DmnDesigner` (with mocked `dmn-js`, same pattern as `BpmnDesigner`), `DmnDefinitionList` (same cases as `ProcessDefinitionList`, plus the `decision_id` column), `HomePage` (tab switcher shows/hides the other list respectively).
- **Real browser test this session** (ephemeral, non-project Docker Playwright instance `mcr.microsoft.com/playwright:v1.48.0-jammy`, no permanent entry in this repo — see `PROGRESS.md`): complete end-to-end run against the real stack, freshly rebuilt via `docker compose up --build` — login, tab switching, "create new" in the DMN designer, `dmn-js` canvas renders correctly (decision table incl. both rule rows), file import of a real DMN 1.3 fixture, save (success message incl. version number, redirect to the new `id`), returning to the list confirms the new entry incl. correct `decision_id` — no console errors at any step. The test entry was subsequently deleted again. This also uncovered and fixed the `loadedForId` bug described above, as well as a pure environment issue (not code-related) that was diagnosed and repaired: the Keycloak test account `config-admin` was missing `email`/`firstName`/`lastName` (which triggered Keycloak's "Account is not fully set up" on direct grant; `users-admin` was unaffected) — already present in the running dev stack before this session, not caused by P14-S4, fixed after the fact via the Keycloak admin API.
- **Since Post-Roadmap Phase 21 Session 4** ([ADR 0087](../adr/0087-bpmn-import-review-gate.md)): `isPendingApproval()`/the new `handleSave()` branch are confirmed type-correct via `typecheck`/`lint`/`build`, the existing 39 Vitest tests remain unchanged and green — **no** new dedicated test for the `pending_approval` branch itself (this save flow previously had no test coverage either), an accepted gap, see ADR 0087 "Consequences".

## Open Points

- **No validation of referenced object types/folder targets on import** — no currently existing task type in this system holds such references (see `docs/services/workflow-service.md` "Open Points"). Import validation is limited to client-side `importXML()` failure and the existing `workflow-service` server-side check (`422` on unparsable BPMN).
- **Federation Hub / federated process steps implemented since P6-S9** (see "Federated Step Properties Panel" above, `docs/services/federation-hub-service.md`) — deliberately **no real swimlane editing** (target installation is a property of the process step, not of a lane), no validation of whether the entered `targetProcessType` actually exists on the target installation (the designer has no way to know this — only the hub address book, not the process catalogs of the target installations).
- **No connection to configuration export/import** (7.3) — process definitions are currently not part of a cross-device configuration export, a possible later extension.
- **No rollback endpoint/no family deletion** (see ADR 0027 "Consequences") — an older version can be opened/exported but not directly "restored as newest"; deletion remains per version.
- **No race-condition lock during version assignment** (see ADR 0027) — accepted for a baseline without high-frequency concurrent saves.
- **No permanently set-up browser E2E in the repo** — no Chrome/Chromium permanently installed (Playwright installation explicitly declined by the user, see earlier sessions), verification instead runs on demand via an ephemeral, non-project Docker Playwright instance (no repo/environment entry), as practiced in P14-S2 and again in P14-S4.
- **DMN (P14-S4)**: no validation of whether a `camunda:decisionRef` entered in the BPMN designer actually corresponds to an existing DMN family — the designer has no way to know this (workflow-service only rejects it server-side at actual save/instance start time, see `docs/services/workflow-service.md`). No automatic "this BPMN file references these DMN families" cross-reference in the overview.
