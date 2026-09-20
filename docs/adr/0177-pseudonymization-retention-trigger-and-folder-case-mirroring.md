# 0177 — Attribute-pseudonymization: retention-expiry auto-trigger, folder-/case-service mirroring

**Status:** accepted
**Context:** P58-S1 (Phase 58, "Pseudonymization Completion & Small Polish Bundle" — first session
of the sixth gap-analysis round's final phase). Closes the two residual gaps ADR 0156 itself named
in its own "Consequences" as deliberately deferred: "no automatic trigger on retention expiry" and
"`folder-service`/`case-service` have no equivalent mechanism".

## Decision

**Part A — `document-service`: a new, mutually-exclusive `retention_pseudonymize` flag.** Alongside
the existing `retention_until`/`full_deletion` pair, `Document` gains a third field,
`retention_pseudonymize: bool`. When retention expires (`_retention_poll_loop`'s existing
"due retention action" phase), the poll loop now checks this flag FIRST, before `full_deletion`:
if set, every attribute the object-type schema marks `personal_data: true` and that still carries a
live value is automatically pseudonymized (new `_pseudonymize_eligible_attributes` helper, reusing
the exact same crypto/vault primitives the manual `POST .../pseudonymize` endpoint already uses), then
`retention_until`/`retention_pseudonymize` are cleared (new `repository.mark_retention_pseudonymized`)
so the document isn't reprocessed on the next tick. `PUT /documents/{id}/retention` rejects setting
both `full_deletion` and `retention_pseudonymize` together (`422`) — they are alternative terminal
actions for the same retention-expiry event, not composable.

**Part B — `folder-service`: full mirror, including the auto-trigger.** `folder-service` already has
an identical `retention_until`/`full_deletion`/`_retention_poll_loop` shape to `document-service`
(since P7-S1b) — so it gets the complete mirror: its own `PseudonymizedAttribute` vault table, its own
`crypto.py` (AES-256-GCM, deliberately duplicated per-service per this project's established
single-purpose-crypto-module convention, see ADR 0156's own rationale), its own
`DMS_ATTRIBUTE_PSEUDONYMIZATION_KEY` (a SEPARATE key from `document-service`'s — no shared trust
relationship between two services' key material), the same three endpoints
(`POST .../pseudonymize`/`.../reveal`, `GET .../pseudonymized`), the same `retention_pseudonymize`
field/poll-loop branch, and `hard_delete_folder` gains the same vault-cleanup fix ADR 0169 already
gave `document-service` (the FK has no `ondelete=`, so a folder with vault entries would otherwise hit
a real Postgres FK violation on forced deletion/trash-purge).

**Part C — `case-service`: manual-only mirror, no auto-trigger.** `case-service` gets the same vault
table/crypto module/three endpoints, reusing the exact same two global RBAC capabilities. It does
**not** get an auto-trigger or a `retention_pseudonymize` field: `case-service` has no
`retention_until`/`full_deletion` mechanism at all (confirmed via grep — cases are never subject to a
retention deadline, only records disposal via `archive_after`/`archive-request`, a conceptually
different, already-built mechanism), so there is no poll-loop phase to hook an automatic trigger into.
It also needs no `hard_delete_case`-side vault cleanup: a `Case` row is never physically removed in this
codebase (only closed/archived), so the FK-violation risk ADR 0169 closed for `document-service` and
Part B closes for `folder-service` does not exist here.

**RBAC: capability reuse, no `permission-service` change.** Both mirrors reuse the exact same two
global domain-admin capabilities ADR 0156 already created (`admin.attribute_pseudonymization`/
`admin.attribute_reveal`, roles `domain-admin-pseudonymization`/`domain-admin-pii-reveal`) — the same
established precedent `admin.retention`/`admin.legal_hold` already follow across `document-service`
and `folder-service` (these are installation-wide capabilities, not resource- or service-scoped, so an
already-granted principal works unchanged against the new endpoints too).

## Rationale

- **Why `object_type_client` is an explicit parameter of `_pseudonymize_eligible_attributes` rather
  than reached from `app.state`**: found during this session's own testing — a plain `async def` test
  function does not share `TestClient`'s internal event loop, so calling `app.state.object_type_client`
  (an `httpx.AsyncClient` bound to that loop) from a separately-scoped async test raised a real
  `RuntimeError: ... bound to a different event loop`. Matching the already-established pattern
  `retention_actions.py`'s functions already use (`storage`/`document_client` passed explicitly, never
  read from `app.state`) fixed this and made the helper independently unit-testable, mirrored
  identically in `folder-service`'s own copy of the function.
- **Why mutual exclusivity is enforced with a `422`, not silently resolved by giving one priority**: both
  `full_deletion` and `retention_pseudonymize` are equally valid, deliberately chosen terminal actions
  for the same retention-expiry event — silently picking one on behalf of an admin who (mistakenly) set
  both would hide a real configuration mistake instead of surfacing it.
- **Why `case-service` isn't also given its own `retention_until` mechanism just to enable the
  auto-trigger**: out of scope for this session — case-service's own lifecycle (open → closed →
  optional archival) is a deliberately different shape from document/folder retention (Concept 2.3 vs.
  5.2/5.2a), and inventing a retention concept for cases solely to unlock this one feature would be
  scope creep against a session whose actual mandate was closing ADR 0156's two named gaps, not
  redesigning case-service's lifecycle.
- **Why a separate encryption key per service, not one shared key**: identical reasoning to ADR 0156's
  own choice not to reuse `archival-service`'s key — three services with no existing trust relationship
  between their key material, reusing one key across all three would couple their blast radius for no
  operational benefit.

## Consequences

- `docs/adr/0156-attribute-pseudonymization-reversible-vault.md`'s own "Consequences" section gets both
  named Open Points struck through and closed, pointing here.
- New tables: `folder.pseudonymized_attribute`, `case.pseudonymized_attribute` (both `create_all`-managed,
  no ad-hoc migration needed for a genuinely new table). New column: `document.document.retention_pseudonymize`,
  `folder.folder.retention_pseudonymize` (both via the established `ALTER TABLE ... ADD COLUMN IF NOT
  EXISTS` ad-hoc migration pattern, since these are additions to already-existing tables).
- New settings: `DMS_ATTRIBUTE_PSEUDONYMIZATION_KEY` on `folder-service`/`case-service` (document-service
  already had its own since ADR 0156) — a pseudonymize/reveal call without it configured fails `503`,
  same convention as `document-service`.
- New events: `document.retention.pseudonymized`/`folder.retention.pseudonymized` (auto-trigger only,
  fired with the list of attribute names actually pseudonymized this tick — omitted when the list is
  empty, e.g. a document/folder whose flag was set but had nothing eligible left), plus
  `{document,folder,case}.attribute.pseudonymized`/`.revealed` for the new manual endpoints on
  folder-service/case-service (document-service's own equivalents already existed).
- **Tests**: `document-service` (+9 in `test_attribute_pseudonymization.py`: mutual-exclusivity `422`,
  field round-trip, 3 direct unit tests of the new poll-loop helper) — 398 total. `folder-service`
  (new `test_attribute_pseudonymization.py`, 21 tests mirroring document-service's shape 1:1) — 164
  total. `case-service` (new `test_attribute_pseudonymization.py`, 18 tests, manual-only, no
  retention/auto-trigger tests since the mechanism doesn't exist there) — 86 total. All three rebuilt/
  redeployed, live-verified against the real running stack (real document/folder/case with a
  `personal_data` attribute, pseudonymize → placeholder → reveal → original value, cleaned up
  afterward).
