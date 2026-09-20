# 0173 — Gating `migration-service`'s step-callback endpoints on the verified `workflow-service` caller identity

**Status:** accepted
**Context:** P54-S2 (Phase 54, "Critical Authorization Bugs" — second session of the fifth gap-analysis
round). Found by this round's live-code security sweep: `migration-service`'s six
`/transfers/{id}/steps/*` endpoints (`step_lock`, `step_copy`, `step_verify`, `step_release`,
`step_delete_source`, `step_dry_run_check`) have no gate at all — unlike every sibling
`/transfers`/`/paired-installations` endpoint in the same file, all of which carry a
`dependencies=[Depends(license_gate(...))]`. These six are meant only as `connector_call` BPMN
service-task callback targets (`resources/migration_transfer.bpmn`/`migration_dry_run.bpmn`), but
`migration-service` registers with `registry-service` and is reachable through the gateway by any
authenticated user, at `/api/migration-service/transfers/{id}/steps/*`.

Worse than a missing gate: `step_delete_source` → `transfer_steps.delete_source` calls `folder-service`'s
trash endpoint with a **hardcoded** `X-DMS-Principal: migration-service` header — the established
system-identity convention this service already uses elsewhere (`LocalDmsClient._PRINCIPAL_ID`, same
string), not itself a new problem. Combined with the missing inbound gate, though, it meant any
authenticated user who knew or guessed a `transfer_id` could `POST
/api/migration-service/transfers/{id}/steps/delete-source` directly — bypassing the BPMN process
entirely, the transfer's own approval gate at creation, and their own permissions on the source folder —
and have `folder-service` trash it anyway, since `migration-service`'s own elevated identity performs the
call.

## Decision

**Gate all six step endpoints behind a new `_require_workflow_service_caller` dependency, checking
`X-DMS-Principal == "workflow-service"` — and make `workflow-service`'s generic `connector_call` HTTP
dispatcher (`_handle_connector_task` in `services/workflow-service/src/workflow_service/main.py`) send
that exact header on every such call, unconditionally.** No new secret, no operator-key model — reuse
the established "trusted internal caller sends a fixed system-identity string" convention already used
throughout this codebase (e.g. `workflow-service`'s own `DocumentClient._SYSTEM_PRINCIPAL_HEADERS`,
`mail-connector`'s `X-DMS-Principal: mail-connector`, `migration-service`'s own outbound calls).

## Rationale

- **Why this check is actually robust, not merely security theater**: `migration-service` is reachable
  two ways — (a) directly, container-to-container, when `workflow-service`'s `connector_call` dispatcher
  posts to the `serviceUrl` from the BPMN definition (bypasses the gateway entirely, no bearer token), and
  (b) through the gateway, by any authenticated end user with a real Keycloak session. For path (b), the
  gateway's `proxy()` handler *always* overwrites any client-supplied `X-DMS-Principal` with the verified
  JWT's own `claims.get("sub", "")` before forwarding (`headers.update(identity_headers)` runs after
  `filter_headers`, unconditionally, for every non-`public_routes` path) — a real end user's principal is
  their own Keycloak `sub`, which can never legitimately equal the literal string `"workflow-service"`.
  So the check is unspoofable for exactly the caller class this session needs to exclude, and requires
  only that path (a)'s currently-headerless call gets the same fixed string.
- **Why extend the GENERIC `connector_call` dispatcher rather than adding this only for `migration-
  service`'s specific BPMN files**: `_handle_connector_task`'s own docstring already frames itself as
  "completely generic, no knowledge of the calling service (migration-service is the first, but not the
  only conceivable user)". Sending a fixed `X-DMS-Principal: workflow-service` header on every
  `connector_call` POST is a small, unconditional, backward-compatible addition at the one shared call
  site — every future `connector_call` target automatically gets a verifiable caller identity for free,
  at zero marginal cost, rather than each new target having to invent its own bespoke secret.
- **Why NOT a `hub_operator_key`/`fleet_operator_key`-style static secret (ADR 0162/0172) instead**: those
  gate services with no other legitimate caller at all — pure operator/console surfaces. These six
  endpoints DO have one legitimate, already-identifiable caller (`workflow-service`, driving an
  already-approved BPMN transfer process) — checking that specific identity is a strictly better fit than
  a shared static secret that any holder could also replay outside the intended BPMN flow.
- **Why NOT touch `transfer_steps.delete_source`'s outbound `X-DMS-Principal: migration-service` call**:
  that's the same, already-established system-identity convention this service's `LocalDmsClient` already
  uses for every other outbound call (`_PRINCIPAL_ID = "migration-service"`) — not itself the bug. Once
  the inbound gate above closes off any caller except the legitimate, already-approved BPMN flow, this
  elevated outbound identity is exactly the expected, accepted shape (the same as every other
  system-to-system call in this project), not a residual issue.
- **Why NOT also gate `/transfers`/`/paired-installations`**: those already have `license_gate(...)`
  (a deliberate license-tier check, distinct concern) and are meant to be called by a real, RBAC-checked
  end user/admin through the gateway — that's their intended caller, unlike the six step endpoints.

## Consequences

- Every `POST /transfers/{id}/steps/*` call now requires `X-DMS-Principal: workflow-service` — a real end
  user (their own Keycloak `sub` as principal) gets `403`, a direct/unauthenticated caller with no header
  at all gets `403`, only `workflow-service`'s own `connector_call` dispatcher succeeds.
- Every existing step-endpoint test needed the header added; new regression tests
  (`test_step_lock_requires_workflow_service_caller`, `test_step_lock_with_wrong_principal_returns_403`)
  prove both the missing-header and wrong-principal cases are rejected before reaching any business logic.
- `workflow-service`'s `_handle_connector_task` now sends one fixed header on every `connector_call`
  POST — a strictly additive change; no existing `connector_call` target (only `migration-service` today)
  reads or rejects on this header, so nothing breaks for any target that doesn't yet check it.
- Live-verified directly against the real running stack: a real BPMN `migration_transfer`/`migration_dry_run`
  process still completes its step callbacks successfully (the header now present and accepted); a direct
  `curl` to a step endpoint with no header, and with a wrong `X-DMS-Principal`, both return `403` before
  reaching the transfer lookup.
