# 0203 — P70-S1: Document Declassification — Scoping

**Status:** accepted (scoping only — no code)
**Context:** P70-S1 (Phase 70, first session, ninth gap-analysis round). `document-service`'s `PUT
/documents/{id}/classification-level` (`main.py:2524-2553`) is set-or-raise only —
`repository.set_classification_level` (`repository.py:485-505`) rejects any `new_rank < current_rank`
with `409` (`CLASSIFICATION_RANK`: `VS-NfD=1, VS-VERTRAULICH=2, GEHEIM=3, STRENG GEHEIM=4`). No
endpoint, workflow, or process anywhere clears/lowers a document's classification once set. ADR 0114/
0115 both already name this as a deliberate, deferred gap ("a separate, heavier administrative process,
not a routine action"). This session is scoping only, per the plan's own Definition of Done — no code,
a decision record for P70-S2 to build against. `Konzept.md` has no text on declassification at all
("Deklassifizierung"/"herabstufen"/"declassif" — zero occurrences); the process shape is genuinely open.

## What the plan's own hypothesis got right, and the one thing it undersold

The plan's text speculated: a dedicated capability distinct from `admin.deletion_classified`, a
mandatory dual-control/four-eyes gate reusing ADR 0022's generic mechanism, a full audit trail entry
distinct from a normal metadata update, and a decision on single-step vs. pass-through-each-level.
Verified against the real code: **mostly right**, with one correction and one real gap the hypothesis
missed.

**Correction**: "a full audit trail entry distinct from a normal metadata update" is **already true
today**, not something P70-S2 needs to build. Classification raises already publish a dedicated
`document.classification.changed` event (not the generic `document.metadata.updated`) — `audit-service`
subscribes to the whole `document.>` wildcard, so no new plumbing is needed; declassification can reuse
this exact event type with an added payload marker.

**Undersold complexity — what "mandatory" concretely means in this codebase**: the plan's phrasing
("mandatory dual-control gate") reads like ADR 0022's ordinary per-installation-configurable
`requires_approval` toggle (used by `document.delete`/`document.force_delete`/`document.force_unlock`,
default off) — but that mechanism is opt-in, not mandatory, by design (ADR 0022's own point: configurable
per action type, not globally enforced). The one genuinely mandatory precedent in this codebase is
`auth.superuser.activate` (ADR 0023): it has **no synchronous execution path in code at all** — the only
way to reach it is through an approval request, executed exclusively by the
`permission.approval.approved` consumer once a second person approves. "Mandatory" here means removing
the bypass from the code entirely, not setting a config flag to `true` — a materially more invasive
shape than every other four-eyes-gated action in `document-service` today, and one with no synchronous
fallback even for ops/testing convenience.

**A real, unaddressed question the plan's "lower the field" framing skips entirely**: `DocumentVersion`
snapshots capture `classification_level` at creation time and are never retroactively rewritten when the
current level is later raised (ADR 0114's own documented behavior). Declassification must explicitly
decide whether it also rewrites past version snapshots, or only the document's current field — "lower
the classification" is ambiguous between these two without a decision.

## Decision for P70-S2

1. **New capability `admin.declassification`** (new domain-admin role, e.g. `domain-admin-declassification`)
   — not a reuse of `admin.deletion_classified` (which governs *purging* already-classified documents, a
   materially different sensitive action per ADR 0114's own "two distinct sensitive actions, two distinct
   domains" precedent, also used for `admin.classification` vs. `admin.object_config`).
2. **Mandatory, not configurable** — mirrors `auth.superuser.activate`'s shape (ADR 0023), not the
   optional `requires_approval` toggle: no synchronous execution path exists in code for a downgrade: a
   request always creates a pending `ApprovalRequest`, executed only by `document-service`'s own
   existing `permission.approval.approved` consumer once approved. `ApprovalActionConfig` gains a
   `required_permission` for this action type (e.g. `declassification.approve`), same mechanism ADR 0023
   added for break-glass, so the approver must hold a distinct capability from the initiator's
   `admin.declassification` — a second person, not just any second click.
3. **Single-step-only by default**: reject a multi-level jump (`new_rank == current_rank - 1` required,
   `422` otherwise) — the safer default given this is an infrequent, heavy administrative action; a
   direct multi-level drop is a one-line relaxation (`repository.py:497`'s comparison) if a future
   session's policy needs it, deliberately not built now to keep the initial cut conservative.
4. **Reuses `document.classification.changed`** (not a new event type) with an added payload marker
   distinguishing a lowering from a raise, e.g. `{"classification_level": ..., "direction": "lowered"}`.
5. **`DocumentVersion` snapshots stay untouched** — declassification changes only the document's current
   `classification_level` field, exactly like a raise already does (ADR 0114's existing, unchanged
   precedent: a version snapshot reflects the classification at THAT version's creation time, not a
   live-updated mirror of the current value). Explicitly NOT retroactive, so a historical version export
   still shows the classification level that was actually in force when it was created — reduces this
   session's scope to the current-field change only, the same boundary ADR 0114 already drew for raises.

## Consequences

- P70-S2's own DoD (already set in `IMPLEMENTATION_PLAN.md`) applies unchanged: a new ADR, tests proving
  the mandatory gate actually has no bypass (not just that a config flag defaults to on), and
  `docs/services/document-service.md` updated.
- No code, no tests, no doc corrections beyond this ADR in P70-S1 itself.
