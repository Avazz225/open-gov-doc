# 0141 — Minimal case-browsing UI: `user-ui`, list+detail, import always attaches to the current case

**Status:** accepted (P34-S3, see Phase 32+ in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 34 Session 3 (XDOMEA/XJustiz completion), affects `user-ui`

## Decision

`user-ui` gains a new "Umlaufmappen" pane (`CasesPane.tsx`) reachable from a new, ungated icon-rail
entry: a flat list of `case-service` cases with a status filter, and clicking a row opens a detail view
showing the case's fields, its document references, and — gated on `archival.write` — four action
buttons: XDOMEA export, XJustiz export, XDOMEA import, XJustiz import. Both imports always attach their
resulting documents to the currently-open case; neither can create a new case via the UI.

## Rationale

- **`user-ui`, not `reviewer-ui`** — the plan's own suggested precedent ("Phase 29's established
  precedent for 'Vorgang' detail views" in `reviewer-ui`) was verified against the actual code before
  building anything and found not to transfer: `reviewer-ui`'s `InstanceDetail.tsx` renders
  `workflow-service`'s `ProcessInstance`, not `case-service`'s `Case` — ADR 0110 itself already notes
  this as a correction made during that session ("the two are unrelated concepts"). With the suggested
  precedent disproven, the choice was put to the user directly via `AskUserQuestion` rather than
  guessed; the user chose `user-ui`. This also matches where the XDOMEA/XJustiz document-level export
  buttons already live (ADR 0126/ADR 0140) and where the case's own documents are actually opened and
  worked with day to day.
- **List+detail, not a flat list** — a flat list has nowhere to put the four export/import actions
  without either cluttering every row or picking one case arbitrarily; a detail view is the natural,
  minimal place for per-case actions, and follows the same list→detail shape already used elsewhere in
  `user-ui` (e.g. `AussonderungPane`'s records-quarantine list).
- **"Umlaufmappe"/"Umlaufmappen" terminology** — this project already has an established German term for
  `case-service`'s `Case` concept, discovered via grep of `AussonderungPane.tsx`'s
  `kindCase: "Umlaufmappe"` i18n key. An initial draft used "Fälle"/"Fall" before this was found and
  corrected; using the existing term keeps the UI internally consistent instead of introducing a second,
  competing name for the same concept.
- **Ungated browsing, gated actions** — the pane itself has no permission gate, matching `case.read`'s
  "everyone" default (ADR 0070); only the four export/import buttons check
  `permissions.includes("archival.write")`, the same capability that already gates the equivalent
  document-level buttons in `PreviewPane.tsx` (ADR 0126/ADR 0140). This avoids inventing a second
  gating rule for the same capability.
- **Import always attaches to the current case, never creates a new one** — both `archival-service`
  import endpoints (`/xdomea/import`, `/xjustiz/import`, ADR 0128/ADR 0139) support an alternative
  "create a new case via `process_definition_id`" target, but no process-definition-picker UI component
  exists anywhere in the codebase (confirmed via research before building). Building one from scratch
  was out of scope for a "minimal" case-browsing session; since the import UI lives inside an
  already-open case's detail view, "attach to this case" is also the natural, unambiguous default for
  where a user reaches this form from. Creating a new case via import remains API-only, same as it was
  before this session.
- **Document list shows raw `document_id`, not resolved titles** — `CaseDocumentReference` (returned by
  `GET /cases/{id}/documents`) carries no title; resolving one would mean an extra `getDocument()` call
  per row on every case-detail render. Clicking a document ID still opens it via the existing
  `getDocument()` + `onOpenDocument` flow, so the ID is only ever a label, not a dead end. A reference
  whose document was deleted (`document_deleted_at` set) renders a non-clickable "Dokument gelöscht"
  placeholder instead of a button.

## Consequences

- **Tests**: `user-ui` +10 (260 total, up from 250) — new `cases-pane.test.tsx` covers the empty state,
  list→detail navigation, the back button, the deleted-document placeholder, opening a document from the
  list, permission-gated hiding of all four action buttons, both exports, and both imports.
- **Live-verified in a real browser** against the real, rebuilt running stack: created a real case and a
  real case-document reference via the API, logged in, navigated to the new "Umlaufmappen" icon-rail
  entry, opened the case, confirmed the detail view rendered correctly (screenshot), triggered real
  XDOMEA and XJustiz export downloads, then used the saved XDOMEA export ZIP to exercise the XDOMEA
  import form end to end, confirming the expected "0 Dokument(e) importiert." result — correct, not a
  bug, since the case's only document reference had no `snapshot_version_number` yet (the same
  pre-existing OPEN-case export-exclusion gate found and documented during P34-S1's own live
  verification). The XJustiz import button was not separately exercised live: it shares the exact same
  form component and code path as the already-proven XDOMEA import (only the underlying API call
  differs), and has full unit-test coverage of its own.
- **No backend change** — this session is frontend-only, consuming already-shipped `case-service` and
  `archival-service` endpoints unchanged.
- ~~**Still deferred**: a real `case-service` browsing UI in `reviewer-ui` remains out of scope (still
  genuinely open as of Phase 65+'s gap-analysis round — `user-ui`'s `CasesPane.tsx` has no `reviewer-ui`
  counterpart).~~ — **closed in Post-Roadmap Phase 74 Session 1**: `reviewer-ui` gained its own
  `CasesPane.tsx` (own route `/cases/`, own `Shell.tsx` tab) — a deliberately smaller cut (no favorites,
  no XDOMEA/XJustiz archival export/import) but, going further than this session's own `user-ui` version,
  with resolved document titles doubling as real download links, since `reviewer-ui` has no document-
  preview/workspace infrastructure of its own to open a document into. See `docs/services/reviewer-ui.md`
  "Case Browsing". ~~a process-definition picker for import-creates-new-case remain out of scope~~ —
  **closed in Phase 42 Session 1**: `CasesPane.tsx`'s `NewCaseImportSection`. ~~`case-service`'s own
  per-case RBAC resource type (vs. today's delegation-scoping workaround) is already tracked separately
  as P35-S2~~ — **closed by ADR 0144** (P35-S2).
