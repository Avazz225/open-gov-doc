# 0169 — Pseudonymization vault retention: tied to the document's own hard-delete lifecycle

**Status:** accepted (Phase 52 Session 3, see Phase 52 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 52 Session 3 (Dependency-Resolved / Overdue Completions), affects `document-service`
only. [ADR 0156](0156-attribute-pseudonymization-reversible-vault.md)'s own explicitly deferred gap.

## Decision

ADR 0156 built reversible attribute pseudonymization (5.2) but deliberately left vault-entry retention
unsolved, naming two options in its own Consequences without picking one: (a) tie it to the same
retention/legal-hold machinery `document-service` already has for documents, or (b) a new, independent
per-vault-entry `retention_until`/poll loop (the shape `auth-service`'s fine-grained-tracking retention,
ADR 0157, later happened to use for an unrelated feature).

**This session chooses (a): a vault entry's lifetime is tied exactly to its own document's row
lifetime — it is deleted the moment the document itself is hard-deleted (`repository.
hard_delete_document`), through whichever of the three existing paths gets it there (forced deletion,
trash-expiry purge, or records-quarantine auto-delete), and not before.** No new table, no new config,
no new poll loop.

This directly implements Konzept 5.2's own wording, which ADR 0156's Decision quoted only in part: *"...
Pseudonymisierung/Schwärzung einzelner personenbezogener Attribute statt Hardlöschung des gesamten
Dokuments, wo eine Aufbewahrungspflicht besteht; **echte Löschung nur, wo keine gesetzliche Pflicht
entgegensteht**"* — real deletion only where no legal obligation stands in the way. A document's own
`retention_until`/legal-hold/trash-restore-window machinery is already the system's one existing,
correct answer to "does a legal obligation currently stand in the way of deleting this document" — a
vault entry belongs to exactly one document and has no independent existence or legal basis apart from
it, so reusing that same answer for the vault entry is not an approximation, it is the literally correct
scope: the vault entry's justification for existing (rather than being hard-deleted immediately) was
always "the document it belongs to still needs to exist" in the first place.

## Rationale

**Why not option (b), an independent fixed-N-day retention config**: that shape fits data with its own,
document-independent retention rationale — `auth-service`'s tracking-session data (ADR 0157) is
deliberately short-lived regardless of any other record's state, by design. A pseudonymization vault
entry has no such independent rationale; asking an operator to configure "how many days should a vault
entry survive" as a number disconnected from the actual document's own retention/legal-hold state would
be a second, parallel, harder-to-reason-about retention clock for the SAME underlying legal question
this system already answers once, per document, today. It would also not close the concrete bug below on
its own (a vault entry could still outlive its document's own hard-deletion, or be purged too early
while the document is still under legal hold).

**A real, latent correctness bug found and fixed as part of choosing this option, not a separate
finding**: `hard_delete_document`'s dependent-row cleanup (versions, an orphaned lock, legal-hold
history, records-quarantine history) never included `PseudonymizedAttribute`, and that table's FK to
`Document.id` has no `ondelete=` clause. Every one of `hard_delete_document`'s three callers
(`retention_actions.execute_forced_deletion`/`purge_expired_trash_entry`/`execute_quarantine_auto_delete`)
would therefore hit an actual, committed Postgres FK violation — not merely leave an orphaned row — the
first time any document with a vault entry was ever forced-deleted, trash-purged, or quarantine-auto-
deleted. Implementing this session's chosen design (add vault-entry cleanup to `hard_delete_document`,
matching the existing pattern for every other dependent-row type) fixes this bug and closes ADR 0156's
retention gap in the same one-line change — they turned out to be the same fix, not two.

**Why the trash-restore window doesn't complicate this**: a soft-deleted document with `full_deletion=
False` is not hard-deleted immediately when `retention_until` passes — `_retention_poll_loop` only soft-
deletes it, and it remains restorable for `TrashConfig.restore_period_days` before the SAME loop's
trash-expiry phase eventually calls `hard_delete_document` on it. Its vault entries correctly survive
that entire window too (consistent with the document itself still being restorable and revealable), and
are purged at the exact same moment the document becomes permanently, irrecoverably gone — never before,
never meaningfully after.

**No new NATS event added for vault-entry purging specifically.** `retention_actions.py` does not
publish events itself (callers in `main.py` do, after each of the three actions above returns) and each
of those three actions already publishes its own document-level event (`document.force_deleted`/
`document.trash_purged`/`document.records_quarantine.auto_deleted`) plus a `DeletionRegisterEntry` —
together already the audit trail for "this document, and everything that belonged to it, is now
permanently gone." A fourth, vault-entry-specific event describing a sub-detail of an event that already
fired would not carry new operationally actionable information, and this project's own convention favors
avoiding that kind of redundant signal.

## Consequences

- **`hard_delete_document` now also deletes every `PseudonymizedAttribute` row for the document**,
  right alongside its existing dependent-row cleanup, inside the same intermediate-flush-before-parent-
  delete pattern already established there.
- **A document with an indefinite retention (`retention_until IS NULL`, e.g. a permanent record) keeps
  its vault entries indefinitely too**, for exactly as long as the document itself is kept — consistent
  with Konzept 5.2's framing (pseudonymization exists to serve a retention obligation; a permanent
  record's retention obligation, by definition, never lapses).
- **No admin-facing change** — pseudonymize/reveal/list endpoints, their RBAC, and their response
  shapes are all unchanged. This session is a backend correctness/retention fix only.
- **Regression tests**: a repository-level test (`test_repository.py`, proving the FK-safe cleanup and
  that the vault entry is actually gone afterward) and a real-database integration test
  (`test_retention_actions.py`, using a real committed `session.commit()` against the real Postgres
  engine — the one that would have actually caught the FK violation this session fixes, unlike a
  rollback-scoped unit test).
