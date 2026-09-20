# 0170 — Case export includes documents for open cases too (current-version fallback)

**Status:** accepted (Phase 52 Session 4, see Phase 52 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 52 Session 4 (Dependency-Resolved / Overdue Completions), affects `archival-service`
only. [ADR 0159](0159-dms-to-dms-xdomea-handoff-implementation.md)'s own documented, deferred finding.

## Decision

`archival_service.general_export.build_case_export_package[_xjustiz]` filtered a case's document
references on `snapshot_version_number is not None` — a field `case-service` only ever sets once, at
case CLOSURE (`repository.close_case`). For a still-OPEN case, every reference was therefore silently
excluded: the function returned a normal `200` with a schema-valid ZIP containing zero documents, no
error, no warning, anywhere in the response. This directly contradicted the function's own stated
purpose — its docstring already said *"unlike disposal, the case does NOT need to be closed; any case
may be exported for handoff"* — and ADR 0127's own original design intent for this export path.

**This session chose the `current_version_number` fallback** (the plan's own option (a)) over adding a
"case must be closed first" precondition (option (b)): `_resolve_export_version(reference)` now returns
`snapshot_version_number` if set (closed case), else `current_version_number` (open case) — the two
fields are mutually exclusive by construction in `case-service`'s own model (`main.py._resolve_reference`
only populates `current_version_number` while `case.status == "open"`; `close_case` only ever sets
`snapshot_version_number` once, at the "open"→"closed" transition), so this is an exhaustive, not
heuristic, resolution.

## Rationale

**Option (b) — requiring the case to be closed first — was rejected because it would have reversed an
already-made, still-valid design decision, not corrected a mistake.** ADR 0127 explicitly designed
general case export to work on any case, open or closed, for the concept's stated handoff use case
("handing off a case at the point a local process closes/finalizes it" is the common case, per ADR 0159,
but not the only legitimate one — e.g. handing off an in-progress case to another authority for
continued processing). The bug was never "open cases shouldn't be exportable" — it was "the code
silently forgot open cases have a different, already-modeled way to know their current document
version." Fixing the actual mistake is smaller, more honest, and doesn't remove functionality ADR 0127
already committed to.

**Why the same filter is left unchanged in `case_pipeline.py` (the disposal pipeline)**: that path is
provably unreachable for an open case — `case-service`'s own `POST /cases/{id}/archive-request` already
raises `CaseNotClosedError` (409) for any case that isn't `status == "closed"`, well upstream of
`case_pipeline.py` ever running. The plain `snapshot_version_number is not None` filter there is
therefore a safe no-op, not a latent bug — `general_export.py`'s copy of the same filter was the actual
mistake, since `general_export.py` has no such upstream gate and explicitly claims to support open
cases. No change needed in `case_pipeline.py`; changing it would be scope creep on code that was already
correct for what it does.

**A reference with neither field set** (e.g. a document reference that was never resolvable at
closure-snapshot time, per `close_case`'s own "remains traceably present" handling, or is not currently
resolvable for an open case either) is skipped — not an error, not included — the same behavior the old
code already had for this edge case, unchanged by this fix.

**Live version resolution for a still-open case is inherently a live-at-export-time snapshot, not a
frozen one** — re-exporting the same still-open case later could include a different document version if
the document was updated in the meantime. This is expected and consistent with `current_version_number`'s
own existing semantics in `case-service` (a live view, recomputed on every read while the case is open)
— not a new inconsistency this session introduces.

## Consequences

- **`build_case_export_package`/`build_case_export_package_xjustiz`** (both XDOMEA and XJustiz) now
  correctly include documents for open cases, via a new shared `_resolve_export_version()` helper.
- **`workflow-service`'s DMS-to-DMS XDOMEA handoff** (ADR 0159's own feature, `archival_client.py`'s
  `export_case`) automatically benefits from this fix with no code change of its own — it already just
  forwards `archival-service`'s response bytes. The handoff task for an open (not-yet-closed) local case
  will now correctly include documents too, closing the exact gap ADR 0159 documented as a known
  exposure for its own feature.
- **`user-ui`'s `CasesPane.tsx`** export buttons benefit the same way, no frontend change needed.
- **Regression tests**: two new tests per message format (`test_api.py`, +4 total) — one proving an open
  case's documents are now included (asserting the correct, live `current_version_number` is what gets
  fetched), one proving a reference with neither field set is still safely skipped, not an error.
- `docs/services/archival-service.md`'s Open Points bullet recording this gap (added during
  [ADR 0159](0159-dms-to-dms-xdomea-handoff-implementation.md)/Phase 43 Session 1) is closed.
