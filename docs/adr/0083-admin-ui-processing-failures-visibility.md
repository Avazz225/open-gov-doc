# 0083 — Admin UI: "permanently failed" visibility + manual restart

**Status:** accepted (Session 7 of 7, see Phase 20 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap Phase 20 Session 7, affects `admin-ui`, `rendering-service`, `ocr-service`

## Decision

The five resilience sessions of this phase (ADR 0078–0082) made `failed_permanent`/`delivery_failed`
visible server-side and retry-capable, but without any admin UI integration. This session closes the
gap for the four services explicitly named by the plan (`archival-service` already having an existing
admin UI page, `notification-service`/`rendering-service`/`ocr-service` previously without any UI
visibility) — deliberately **without** `federation-hub-service`, which the plan does not name at this
point.

1. **`ArchivalTransfersView`** (already existing) gets `failed_permanent` as a new filter option in
   both sections (document and circulation-folder disposal) as well as a "retry" button that only
   appears for this status (`POST .../retry`, already present server-side since ADR 0078).
2. **New, shared page `/processing-failures/`** (`ProcessingFailuresView`, three independent sections:
   notifications, renditions, OCR results) instead of three separate pages — each section loads
   exclusively `status=failed_permanent` records of the respective service and offers a restart button
   per row.
3. **`rendering-service`/`ocr-service`: `GET /renditions`/`GET /ocr-results` get `document_id` as
   optional** (previously a required parameter) plus a new `status` query parameter — without this, a
   cross-document "all failed renditions/OCR results" view would not have been technically possible.
   `notification-service`'s `GET /notifications` already had an optional `status` filter and needed no
   change.

## Rationale

- **Why ONE shared new page instead of three**: none of the three services already had a suitable
  existing admin UI page into which a small section would naturally fit (unlike `archival-service`) —
  the plan explicitly allows "a new small section instead of its own page." Three entirely new
  individual pages for the same kind of content (list + restart button) would be unnecessary
  navigation fragmentation; a shared page with three sections follows the same, already established
  multi-section pattern as `ArchivalTransfersView` (document/case section on one page).
- **Why `document_id` is made optional on `rendering-service`/`ocr-service` instead of a separate admin
  endpoint**: both endpoints are already gated via `rendering.read`/`ocr.read` (`permission-service`,
  since ADR 0073) — a call without `document_id` is subject to the same permission check as with it,
  creating no new, ungated attack surface. A separate `/admin/...` endpoint would have duplicated the
  same permission without a real security benefit.
- **Why no new, dedicated badge color for `failed_permanent`**: `admin-ui`'s CSS currently only knows
  two badge variants (`ok`/`down`, see `globals.css`), maintained consistently across three theme
  variants (light/dark/high-contrast). Adding a third variant just for this session would be scope
  creep beyond a pure visibility/restart session — the distinction from `failed` instead happens via
  the (already translated, clearly distinguishable) status text itself ("Failed" vs. "Permanently
  failed") and the restart button, which is only visible for `failed_permanent`.
- **Why `federation-hub-service` is NOT part of this session**: the plan's P20-S7 line explicitly names
  only archival/notification/rendition/OCR failures, not handover — consistent with ADR 0081's own
  scope (only initial delivery, not the result return path). Admin UI visibility for failed handovers
  would be a sensible independent follow-up session, not part of this one.

## Consequences

- **`ArchivalTransfer`/`CaseArchivalTransfer` frontend types** get the previously missing fields
  `attempts`/`next_retry_at` (present server-side since ADR 0078, not yet mapped in the frontend).
- **`statusLabel`/`caseStatusLabel` helper functions** were converted to PascalCase-per-word-part
  (`"failed_permanent"` → `"archivalTransfers.statusFailedPermanent"` instead of the previous pattern
  that only capitalized the first letter, which would have produced an unfindable i18n key).
- **Existing `rendering-service`/`ocr-service` callers remain compatible unchanged**: all previous calls
  already pass `document_id` explicitly as a keyword argument or query parameter — making it optional
  is purely additive, not a breaking change.
- **Tests**: `rendering-service` 46 (previously 44, +2), `ocr-service` 53 (previously 51, +2, plus 8
  still `tesseract`-gated skips) — one repository and one API test each for the `document_id`-optional
  path. `admin-ui` 173 (previously 166, +7): 3 new tests in `archival-transfers.test.tsx` (no retry
  button with only `failed`, retry with `failed_permanent` for both document AND case sections), new
  `processing-failures.test.tsx` with 6 tests (loading/displaying all three sections with the `status`
  filter, empty states, unreachable state, one retry test case per section).
- **Verified live against the real running stack** (image rebuild + restart of `rendering-service`,
  `ocr-service`, `admin-ui`): `GET /renditions?status=failed_permanent` and
  `GET /ocr-results?status=failed_permanent` return, without `document_id`, real `failed_permanent`
  records from earlier live verifications in this phase across multiple documents (confirming the
  cross-document filtering with real data, not only synthetic test fixtures);
  `GET /notifications?status=failed_permanent` correctly returns an empty list (no corresponding
  record present); the new `/processing-failures/` route is served by the `admin-ui` container (`200`).
  No interactive browser click-through was performed (this project consistently verifies frontend work
  via `tsc`/`eslint`/`vitest`/`next build` plus real backend live checks, see `CONTRIBUTING.md`
  "Definition of Done" — no Playwright/browser automation tool exists anywhere in the monorepo).
