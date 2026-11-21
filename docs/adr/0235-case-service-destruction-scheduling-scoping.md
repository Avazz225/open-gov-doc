# 0235 — case-service: destruction-scheduling primitive, scoped

**Status:** accepted
**Context:** P82-S2 (Phase 82, second scoping-only session). ADR 0116 (P31-S5, records quarantine) named
`case-service` as "untouched — it has no destruction-scheduling primitive of any kind today (only `status`
open/closed and the archival-only `archive_after`/`archived_at`)," and explicitly deferred building one as
"a materially larger, separate session." This session is that scoping pass — **not** a build session (Phase
82's own DoD: no code diff expected).

## What already exists, verified against current code

- **`Case` has an archival lifecycle, but no destruction lifecycle.** `Case.archive_after`/`archived_at`
  (`services/case-service/src/case_service/models.py`) are resolved at closure time
  (`repository.py:289-302`, from `CaseArchivalConfig.default_archive_after_days_closed`), exposed via
  `GET /cases/due-for-archival` + `POST /cases/{id}/archive` (`main.py:549-562, 689-704`). There is no
  `retention_until`/`full_deletion` equivalent anywhere on `Case`, and no destruction endpoint of any kind.
- **`case-service` has no internal poll loop at all** — unlike `document-service`'s `_retention_poll_loop`
  (`document_service/main.py:271-296`), case-service's archival due-check is a pure query endpoint
  (`GET /cases/due-for-archival`) with no scheduler of its own. **`archival-service` is the actual poller**:
  `archival_service/case_pipeline.py:31` calls `case_client.list_due_for_archival()` and drives
  `POST /cases/{id}/archive` from there (`archival_service/clients.py:227-228`) — case-service is reactive/
  API-driven, archival-service owns cross-service scheduling. This is the established pattern to extend,
  not document-service's internal-loop pattern.
- **Records quarantine (ADR 0116's own subject) is document-level only, and case-service already respects
  it — but only per-document, live, with no case-level state.** `case-service`'s `_resolve_reference`
  (`main.py:304-337`) calls `DocumentClient.has_active_quarantine()`
  (`document_client.py:32-48` → `GET /documents/{id}/has-active-quarantine`) per document reference, every
  time a case is read. There is no case-wide aggregate/rollup field, no case-level quarantine table, and
  `docs/services/case-service.md`'s own Open Points section does not mention this gap at all.
- **The precedent for "a parent aggregating children with independent state" already exists in
  `folder-service`, and it splits by action type**: folders have their own independent
  `retention_until`/`full_deletion`/`LegalHold` (not synced from child documents,
  `folder_service/models.py:41-42, 106-120`), but **forced deletion is blocked, not cascaded**, while a
  subtree still has active children (`folder_service/main.py:94-113` — no automatic cascading forced
  deletion; the tick just retries later). Non-destructive actions (trash/restore) ARE fanned out via RPC
  (`document_client.cascade_trash`/`cascade_restore`). This is the directly applicable precedent for
  destruction specifically: **block, don't cascade.**

## Decision

**Case-level destruction scheduling should mirror the existing archival pattern exactly, and reuse the
existing per-document quarantine check as a blocking precondition — not build a duplicate case-level
quarantine primitive.**

1. **New fields**: `Case.retention_until`/`full_deletion` (same shape as `Document`'s), resolved at
   archival time the same way `archive_after` already is (extend `mark_archived`/`repository.py:367`),
   driven by a new `default_retention_days_after_archived` on `CaseArchivalConfig` (or a sibling config —
   build session's call, both are one-field additions).
2. **New endpoints, mirroring the archival pair exactly**: `GET /cases/due-for-destruction` (same shape as
   `GET /cases/due-for-archival`) and `POST /cases/{id}/destroy`.
3. **New pipeline in `archival-service`, mirroring `case_pipeline.py`**: polls
   `GET /cases/due-for-destruction`, calls `POST /cases/{id}/destroy` — **not** a new internal poll loop in
   case-service itself, consistent with case-service's existing reactive/API-driven shape and
   archival-service's already-established role as the cross-service scheduling owner.
4. **Guard on `POST /cases/{id}/destroy`: block, don't cascade**, following folder-service's forced-deletion
   precedent exactly. Before destroying, re-check every case-document-reference's
   `has_active_quarantine` (the exact call `_resolve_reference` already makes) and refuse the destroy
   (retry later, same as folder-service's tick) if any referenced document is still actively quarantined —
   or, more conservatively, if any referenced document has not itself reached its own independent
   destruction point. Build session decides the precise blocking condition's strictness; the mechanism
   (reuse the existing per-reference check, don't build a new one) is the scoped decision here.
5. **Do NOT build a case-level quarantine table** (`CaseQuarantine`/similar). ADR 0116's own records
   quarantine is and should remain a document-level concept; case-service already correctly surfaces it
   per-reference. A case-level quarantine primitive would be pure speculative building — add one later,
   reusing `RecordsQuarantine`'s exact shape, only if a genuinely new requirement emerges for holding a
   case's own record independent of its documents' states (e.g. the case's own audit trail/metadata under
   investigation, not its content).
6. **"Destroy" semantics left to the build session**: a `Case` is a lightweight aggregate (metadata +
   document references, no binary content of its own), unlike a `Document`'s storage-backend physical
   deletion — whether "destroy" means a hard delete of the case row or a soft tombstone is a build-time
   decision that does not change the scheduling mechanism above.

## Consequences

- The build session needs no further scoping: data model change, endpoint shapes, the orchestrating
  service, and the blocking precondition are all specified. Only the destroy-semantics and blocking-
  strictness details above are left as build-time judgment calls, not open architectural questions.
- `docs/services/case-service.md`'s Open Points should gain (and, once built, lose) this item — left for
  the build session, per this project's convention of updating docs alongside the change that closes a
  gap.

## Build — P89-S1 (case-service part)

Built as decided. The judgment calls this ADR left to the build session:

- **Config:** two fields on `CaseArchivalConfig`, `default_retention_days_after_archived` and
  `default_full_deletion`, resolved in `mark_archived`.
  - `PUT /case-archival-config` writes them only when sent, which protects them from admin-ui's existing
    two-field form.
- **Destroy semantics:**
  - The case's contents (document references, pseudonymized attribute values) always go.
  - `full_deletion=false` keeps a **tombstone**: id, Vorgangsnummer, dates, `destroyed_at`; name and
    attributes cleared, since both can carry personal data. It drops out of listings and the due list.
  - `full_deletion=true` deletes the row.
  - Referenced documents are untouched.
- **Blocking strictness:** only an **active records quarantine** on a referenced document blocks (`409`).
  An unanswerable check blocks too (`503`, fail closed).
  - The stricter alternative ("every referenced document must itself be past its destruction point")
    was not taken. A case doesn't destroy its documents, so their own retention isn't the case's
    concern, and the stricter rule would make most cases indestructible.
- **Gate:**
  - Unlike the ungated archival callbacks (ADR 0070), the destroy is gated by archival-service's
    existing disposal-callback capability (`document.disposal_callback`, role
    `archival-service-callback`), because it is irreversible.
  - A new `case.disposal_callback` was avoided: seeded roles aren't updated on existing installations.
- **Remaining for P89-S2:** archival-service's destruction pipeline, deletion-register entries, and the
  admin visibility.

## Build — P89-S2 (pipeline, register, restore, admin visibility)

- **Where the register lives:** in case-service, as `case_deletion_register_entry`, written in the
  destroy transaction.
  - This follows document-/folder-service, where each owner keeps its own register.
  - It also means a destruction triggered by any caller (the pipeline, the restore reconciliation) is
    registered.
  - archival-service's `case_destruction` table is the pipeline's own record (waiting, blocked, cancelled,
    done), not the register.
- **Ledger and restore (10.4):**
  - `case.destroyed` joins audit-service's append-only deletion ledger.
  - `scripts/restore.sh` checks a ledger case against the restored DB. A tombstone restored as a
    tombstone is already destroyed, so only a row with `destroyed_at` null counts as back.
  - `POST /cases/{id}/reconcile-restore-deletion` (`dms-admin`, like document/folder) re-runs the
    destruction without the due/quarantine checks, because the destruction already happened once.
- **Blocked is a state, not a failure:** a quarantine refusal is retried every tick without spending
  attempts. A quarantine can last months, and backing off into `failed_permanent` would turn a correct
  refusal into an alarm.
- **Telling the 409s apart:** the pipeline reads the case instead of parsing case-service's message:
  - gone or a tombstone → already destroyed;
  - no longer due → `cancelled`;
  - otherwise → `blocked`.
- **Resource node:** a full deletion publishes `case.resource.deleted`, which permission-service already
  consumed. That closes the leftover noted in P89-S1.
