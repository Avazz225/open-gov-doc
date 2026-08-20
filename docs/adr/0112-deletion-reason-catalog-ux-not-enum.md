# 0112 — Deletion-reason catalog is a UX suggestion list, not a backend-enforced enum

**Status:** accepted (P31-S1, see Phase 31 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 31 Session 1 (eGov feature gap closure — see
[`docs/egov-feature-gap-analysis.md`](../egov-feature-gap-analysis.md)), affects `document-service`,
`folder-service`, `apps/user-ui`, `apps/admin-ui`

## Decision

Both `document-service` and `folder-service` already had a free-text `reason` field on
`PUT .../retention` for forced deletion (P7-S1/P7-S1b), gated optionally-required by
`RetentionConfig.deletion_reason_required`. This session adds a `deletion_reason_catalog: list[str]`
field to each service's own `RetentionConfig` singleton — admin-editable via the existing
`GET`/`PUT /retention-config` endpoints — that powers a `<select>` of curated suggestions plus an
always-available "Sonstiges" (other) choice opening a free-text input in `RetentionPanel.tsx`/
`FolderRetentionModal.tsx`. The `reason` field submitted to the server is unchanged: still a single,
optional string, still validated only for non-emptiness when `deletion_reason_required` is set. The
catalog is **not** an enum constraint — the backend accepts any non-empty string regardless of whether
it appears in the catalog.

## Rationale

- **No enum enforcement, deliberately**: a strict enum would mean every installation's set of valid
  reasons is frozen at whatever the admin configured at the moment of deletion — awkward for the exact
  scenario the catalog is meant to help with (an unusual, one-off reason that doesn't fit any curated
  entry). The reference-system feature this is modeled on itself keeps a designated "other: free text"
  fallback rather than a closed set, confirming this is deliberate product behavior in that system too,
  not an omission to fix here.
- **No new admin-extensible-catalog pattern needed**: research before this session (see the gap
  analysis) found no existing "fixed enum + admin-extensible list + other fallback" combination anywhere
  in the codebase to reuse. Rather than build a new generic mechanism, this session models the catalog
  as the simplest possible admin-editable list — a flat `list[str]` on the existing `RetentionConfig`
  row, matching `UploadConfig.allowed_content_types`'s already-established shape exactly (same
  admin-UI-editable-JSON-column precedent, `document_service/models.py`).
- **Duplicated per service, not shared** — matches the existing, deliberate duplication of
  `RetentionConfig`/`deletion_reason_required`/the entire forced-deletion pipeline between
  `document-service` and `folder-service` (independently configurable since P7-S1b, "an installation
  operator may need different rules for folders than for documents"). Introducing a shared catalog
  service for this one field would be a disproportionate new architectural element for what is,
  precisely because it isn't enum-enforced, a low-stakes convenience list. An admin configuring both
  sections in `RetentionSettings.tsx` (already a single page covering both services) can keep the two
  catalogs in sync by hand if desired.
- **The frontend defaults to the catalog dropdown, not free text, whenever a non-empty catalog is
  configured** — an installation that has invested in curating reasons should see them front and center;
  installations that never configure a catalog (the default, `[]`) see exactly the same plain text input
  as before this session, so the change is invisible until an admin opts in.

## Consequences

- No migration risk to already-stored `reason`/`pending_deletion_reason` values — they were, and remain,
  arbitrary strings; a previously stored value that happens not to match any later-configured catalog
  entry is retained and displayed correctly (the "Sonstiges" free-text branch, pre-filled).
- If an installation later removes a catalog entry that was in active use by many historical deletions,
  nothing breaks — the catalog only drives the dropdown's *offered* choices going forward, it has no
  referential link to already-recorded reasons.
- A future session could add per-object-type catalog overrides (mirroring
  `deletion_reason_required_override`'s tri-state pattern) if a real installation asks for it — not
  built here, no concrete need identified yet.
