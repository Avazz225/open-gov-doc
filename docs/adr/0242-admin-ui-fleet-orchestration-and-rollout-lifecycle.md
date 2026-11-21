# 0242 — admin-ui fleet orchestration, and the rollout lifecycle it needed

**Status:** accepted
**Context:** P88-S1 (Phase 88).

Fleet update orchestration (groups, update plans, rollouts; ADR 0038) has existed in
`fleet-management-service` since P13-S2b, but only through `curl`. P84-S4 (ADR 0234) gave every operator
their own token, so the four-eyes check on a rollout step compares verified identities. That was the
prerequisite for a UI where two people work on the same rollout. ADR 0197 left this UI out of scope.

Wiring the API into a UI showed gaps that `curl` users had worked around by hand:
- A duplicate group name was a raw unique-violation `500`.
- Deleting a group that had members was a foreign-key `500`, and so was deleting an installation that
  was in a group or a rollout.
- Plans and rollouts could not be deleted at all.
- A rollout stayed `running` after its last run completed.
- A rollout that could never finish (an installation was decommissioned, a check could never pass) was
  stuck for good. It also blocked deleting its installation, plan and group.

## Decision 1 — The UI is part of the fleet page, under the same token

`FleetManagementView` (`/fleet-management/`) shows four more sections once a token has been accepted:
- Gruppen (Wellen)
- Update-Pläne
- Rollouts
- Operatoren

They use the token from the last successful "Laden", not the field's live value, so editing the field
doesn't refetch with a half-typed token. The acting identity always comes from that token on the
server; the UI sends no actor fields (ADR 0240).

**What the UI derives from state:**
- **Run actions** come from the run's status and its current step type, which the UI reads from the
  plan:

  | Status | Step type | Actions |
  |---|---|---|
  | `wait_external` | `verify` | Prüfen (`advance`) |
  | `wait_external` | `gate` | Ergebnis melden: an outcome of the four reportable ones, plus a note |
  | `retry_later` | `verify` | Prüfen |
  | `retry_later` | `gate` | Erneut versuchen |
  | `recoverable_failed` | any | Erneut versuchen |
  | `manual_required` | any | Freigeben, Ablehnen (with a reason) |
  | `fatal_contract` | any | Korrektur bestätigen (`acknowledge-fatal`, after a confirmation) |

- **Four eyes:** "Freigeben" is disabled with an explanation when the signed-in operator is the
  proposer. The server refuses it anyway; the UI only says why in advance.
- **The rollout target** previews the server's resolution (group members ∪ extra − excluded) as a
  count before creating.
- **Approval** is offered only on confirmation steps, because the server ignores it on check steps.

**Operators:** admins mint operators (the token is shown once) and revoke them. `bootstrap` gets no
revoke button, because the API refuses it (ADR 0234). Non-admins see the list only.

## Decision 2 — Deleting follows references: links go along, history blocks

- **Group:** its memberships go with it (they are links). A group a rollout was created from blocks
  (`409`), because the rollout still names it.
- **Installation:** its memberships go with it. One that appears in a rollout's runs blocks (`409`): a
  run is history, and the installation can't drop it silently.
- **Plan** (new `DELETE /plans/{id}`): blocks while a rollout uses it.
- **Rollout** (new `DELETE /rollouts/{id}`): allowed for `draft`, `completed` and `cancelled`, together
  with its runs. A `running` rollout is open work and blocks.
- **Duplicate group name:** `409` instead of `500`.

The order to clean up is therefore rollout, then plan and group, then installation. The e2e follows it
and leaves the dev stack's fleet tables empty.

## Decision 3 — Rollouts complete, and can be cancelled

- **Completion:** after any step that advances a run, a `running` rollout whose runs are all `completed`
  becomes `completed`.
- **Cancel:** new `POST /rollouts/{id}/cancel`, allowed only from `running`, moves it to `cancelled`.
  - Runs keep the state they were in, as the record of how far the rollout got.
  - A cancelled rollout can only be deleted. A new attempt is a new rollout (one wave, one rollout, as
    in ADR 0038).
- **Run actions need a running rollout.** `advance`, `mark-done`, `approve`, `reject`, `retry` and
  `acknowledge-fatal` answer `409` unless the rollout is `running`. Before, a draft's runs happened to be
  safe only because `pending` matched no action; a cancelled rollout's runs would have stayed
  actionable.

## Consequences

- fleet-management-service: `InUseError` → `409`; `delete_update_plan`, `delete_rollout`,
  `cancel_rollout`, `complete_rollout_if_done`; nine new tests (52 in total).
- admin-ui:
  - `FleetOrchestration.tsx`, plus API helpers for groups, plans, rollouts, run actions and operators;
  - the `fleetOrchestration.*` i18n keys;
  - `fleet-orchestration.test.tsx` (14 tests) and `e2e/fleet-orchestration.spec.ts`.
- Still open (fleet doc): no metrics for rollout progress, and gate steps are confirmed, not
  remote-controlled (ADR 0038).
