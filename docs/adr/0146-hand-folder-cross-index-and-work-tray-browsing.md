# 0146 — Hand-folder cross-index and installation-wide work-tray browsing

**Status:** accepted (P35-S4, see Phase 32+ in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 35 Session 4 (org-hierarchy & workflow polish, final session), affects `search-service`/`folder-service`/`apps/user-ui`

## Decision

Closes both gaps ADR 0118's own "Consequences" section named as a deliberate, deferred follow-up:
"no cross-case index or search surfacing for hand folders" and "no new admin-ui/user-ui surface for
browsing 'all hand folders'/'all work trays' installation-wide."

1. **`search-service` gains a new cross-folder index of `FolderDocumentReference` rows** (hand folders,
   ADR 0118) — a new `search_folder_reference` table (composite natural key `(folder_id, document_id)`),
   populated by a new consumer subscribed to `folder.document_reference.added`/`.removed` (own durable
   `search-service-folder-references`, third subscription in this service, own "folder" stream). A new
   `GET /folder-references` endpoint (optional `folder_id`/`document_id` filters — the latter also closes
   ADR 0118's named gap of no reverse lookup) lists them, permission-filtered per row against each
   reference's own `folder_id` via `folder.read` (same overfetch-then-batch-check-then-paginate shape as
   the existing `/search` endpoint, just gated by `folder.read` instead of `document.read`).
2. **Work trays get NO new index of their own** — since ADR 0118 already established work trays are not a
   new entity, `search-service`'s EXISTING document index (`search_document`) gains one new column,
   `registered_at` (denormalized from `document-service`'s already-existing draft/pre-registration
   lifecycle, ADR 0113), and the existing `/search` endpoint gains a `registered=true|false` query filter.
   `document.registered`/`document.promoted` are added to the existing document-event trigger list so the
   filter reflects a registration/promotion promptly, not just on the next unrelated metadata touch.
3. **`apps/user-ui` gains one new pane**, `HandFolderOverviewPane.tsx` (new icon-rail entry, ungated like
   the existing "Umlaufmappen" entry — row-level permission filtering already happened server-side) with
   two independent lists: hand-folder references (from the new endpoint) and work-tray documents (`/search
   ?registered=false`). Each row opens the referenced document/folder via the existing
   resolve-then-navigate pattern already used for favorites/teamspaces.

## Rationale

- **A new cross-folder index for hand folders, but NOT a new index for work trays** — genuinely different
  situations, not an inconsistency. A hand-folder reference is a NEW kind of row nothing else in the system
  tracks in aggregate; a work-tray document is an EXISTING document row `search-service` already indexes in
  full, missing only one field. Reusing the existing document index for work trays costs one column and one
  query parameter; building a parallel index would duplicate data `search-service` already has.
- **`folder.document_reference.added`'s event payload gained a new `added_at` field** (a one-line addition
  in `folder-service`, no other consumer of this event exists yet) — the natural alternative, having
  `search-service` reload the full reference via `GET /folders/{id}/document-references`, is unavailable:
  that endpoint is itself `folder.read`-gated (the actual point of ADR 0118), and `search-service` has no
  principal of its own to satisfy it with. Every other event-driven reload in this service (`document.>`)
  works precisely because the equivalent single-resource `GET` is ungated; this is the one place that
  pattern doesn't carry over, so the event payload carries the field instead.
- **The new folder-reference consumer subscribes with `deliver_new=True`, unlike every other consumer in
  this service.** Every other subscription in `search-service` deliberately omits this flag, using
  JetStream's default full-history replay as a free, no-extra-code backfill mechanism for documents (a
  reasonable choice when the consumer is introduced alongside the events it consumes, or when the volume is
  bounded). `folder.document_reference.*` has existed since ADR 0118 (Post-Roadmap Phase 31 Session 7), many
  sessions before this one — a first-ever subscribe here would replay however much history has accumulated
  since then, growing without bound as the installation ages. That cost is real independent of any test
  environment: an installation running for years would face an increasingly slow first-startup catch-up for
  any consumer added late to an old stream. `deliver_new=True` accepts the resulting scope limit
  explicitly — a hand-folder reference created before this feature's rollout is not retroactively
  backfilled into the cross-index — rather than pay an open-ended replay cost or build a separate,
  P35-S2-style explicit backfill loop for what the plan itself frames as a "simple overview page" session.
  `folder-service`'s own table remains the authoritative system of record regardless; only the NEW
  cross-installation browse view has this limit, the existing per-folder view (`GET /folders/{id}
  /document-references`) is unaffected.
- **A real, non-obvious test bug found and fixed while building this**: the first version of this session's
  own `test_folder_document_reference_removed_event_deletes_index_row` fired the add and remove HTTP calls
  back-to-back before starting to poll, racing the two events against the poll's own first check — on a
  fast run, both could already be fully processed (net effect: no row at all) before polling even began, so
  the poll's `_indexed` predicate (looking for the now-already-gone "added" state) would loop until timeout
  and never even reach the removal check. Confirmed via a temporary handler-side print that the consumer
  itself processed both events correctly and near-instantly; the bug was purely in the test's own eager
  request sequencing. Fixed by firing the remove request only after polling confirms the add was indexed.
- **`HandFolderOverviewPane` lives in `user-ui`, not `reviewer-ui`/`admin-ui`** — hand folders and work
  trays are everyday document-management concepts for the same audience `SearchPane.tsx`/
  `HandFolderReferencesModal.tsx` (the existing per-folder reference view) already serve in `user-ui`, not
  a review/approval concern (`reviewer-ui`) or an installation-configuration concern (`admin-ui`).
- **No new permission capability introduced** — both lists reuse permissions that already exist
  (`folder.read` from ADR 0118, `document.read` from the existing `/search` permission filter); this
  session only adds new READS of already-permissioned data, no new write path.

## Consequences

- **Tests**: `search-service` 71 tests (up from 56, +15) — `test_pipeline.py` covers `registered_at`
  denormalization (draft → registered transition via a real `document-service` round trip);
  `test_consumer_integration.py` covers the new consumer end-to-end against the real running
  `folder-service` (add indexes, remove deletes); `test_repository.py` covers the `registered` filter (9
  cases: 3 for work-tray filtering, 6 for the new `FolderReference` upsert/delete/dedup/list-filter
  functions); `test_api.py` covers `/search?registered=` and `/folder-references` (401 without a principal,
  permission-filtered to only readable folders) over real HTTP. `folder-service`'s own 135 tests are
  unaffected (the `added_at` payload addition has no existing consumer to break). `user-ui` +4 tests (new
  `hand-folder-overview-pane.test.tsx`) — empty states, listing a reference and opening its document,
  opening its folder, listing a work-tray document with the `registered: false` filter confirmed sent.
- **Live-verified against the real, rebuilt running stack**: a real hand folder created, `folder.write`/
  `folder.read` granted on it specifically (not `root`), a real document referenced into it, and a real
  draft (unregistered) document uploaded — all confirmed to appear correctly in `user-ui`'s new pane via a
  real browser (Playwright), including opening the referenced document into the document workspace.
- **A hand-folder reference created before this session's rollout will not appear in the cross-index**
  until the underlying reference is touched again (re-added) — an accepted, documented scope limit (see
  Rationale), not a bug; the per-folder view remains fully accurate for it regardless.
- **`GET /folder-references` has the same "not pagination-stable under heavy permission filtering" property
  already documented for `/search`** (`docs/services/search-service.md` "Open Points") — the same
  overfetch-then-filter-then-paginate shape, same accepted limitation, not reintroduced or newly discovered
  here.
- **No admin-ui changes** — nothing about hand folders/work trays is an installation-configuration concern;
  this session is entirely backend + `user-ui`.
