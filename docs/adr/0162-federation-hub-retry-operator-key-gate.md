# 0162 — Gating `POST /handovers/{id}/retry` via the existing `hub_operator_key`

**Status:** accepted
**Context:** P44-S1 (Phase 44, "Security & Correctness Hardening" - the first of the three new gap-analysis
rounds planned after Phase 43). Found during this round's `docs/services/*.md` Open Points sweep:
`federation-hub-service`'s `GET /handovers`/`GET /handovers/{id}` are deliberately ungated (pure metadata,
same rationale as the already-ungated `GET /installations`), but `POST /handovers/{id}/retry` is a real
mutating action - it forces an immediate new delivery attempt against another installation - reachable by
anyone who can reach this service's network address, with **no auth check of any kind** before this
session. This is the one real gap the sweep found in an otherwise already-hardened service.

## Decision

**Gate `POST /handovers/{id}/retry` with the exact same `settings.hub_operator_key` bearer-token mechanism
already used by `POST /installations/{id}/revoke` (ADR 0039) - not a second, dedicated key.** Same
fail-closed default: with no `hub_operator_key` configured (`None` by default in every environment,
including the dev stack in `infra/docker-compose.yml`), the endpoint is fully locked, exactly like revoke
already is.

On the caller side, `apps/admin-ui`'s `ProcessingFailuresView.tsx` (`HandoverFailuresSection`) gains a new
operator-key text input (kept only in component `useState`, never persisted to `localStorage` or any
backend) that the admin must fill before the "Erneut versuchen" button for a handover row is enabled;
`lib/api.ts`'s `retryHandover(id, operatorKey)` now sends `Authorization: Bearer <operatorKey>`.

## Rationale

- **Why reuse `hub_operator_key` instead of a new secret**: `federation-hub-service` has no admin-JWT or
  `permission-service`-capability model at all (`docs/services/federation-hub-service.md`'s own
  description: "this service has no admin-token model, its only gated endpoints... use a different
  mechanism entirely"). Building a first-ever JWT/capability check into this one service, just for this
  one endpoint, would be a materially larger and architecturally inconsistent change for what the sweep
  found to be a bounded, single-endpoint gap - the same "bounded partial mitigation over full redesign"
  principle this project has applied repeatedly (e.g. ADR 0159's payload-size ceiling). A second static
  secret alongside the existing one would only fragment the one existing convention without buying any
  real additional safety - both endpoints are rare, operator-initiated administrative actions once this
  gate exists.
- **Why NOT treat retry as routine/pre-authenticated the way archival-/notification-/rendering-/
  ocr-service's own manual-restart buttons are**: those four services *are* reached through the gateway
  under the calling admin's own JWT, so their retry buttons inherit an already-established
  admin-authentication context. `federation-hub-service` is deliberately the ONE exception - shared,
  cross-installation infrastructure that isn't behind this installation's gateway at all
  (`docs/services/federation-hub-service.md`: "admin-ui calls this service DIRECTLY, not through the
  gateway"). There is no existing authenticated-admin-session concept on this service's side to piggyback
  on; the operator secret is the only trust mechanism it has ever had.
- **Why the admin-ui field is intentionally unpersisted, per-click**: no existing UI anywhere in this
  project currently sends `hub_operator_key` at all - grepping `apps/admin-ui/src/lib/api.ts` found no
  `revokeInstallation` call site either, meaning revocation has to date been a pure `curl`/manual-operator
  action outside admin-ui entirely. This session is the first time admin-ui needs to source this secret
  inline, so there is no established persistence pattern to follow, and a rare, high-stakes secret is a
  poor fit for `localStorage` regardless - typing it per retry click is the same UX cost the revoke action
  already implicitly has (an operator who wants to revoke has to know/hold the secret out-of-band today).

## Consequences

- `POST /installations/{id}/revoke` and `POST /handovers/{id}/retry` now share one operator secret and
  one gating code path shape (`if not settings.hub_operator_key or authorization != f"Bearer {...}":
  raise 403`) - a future third mutating admin action on this service should default to the same
  mechanism unless a concrete reason argues otherwise.
- Every existing `retry_handover` test in `tests/test_api.py` needed the operator-key header added (the
  gate check runs before the handover lookup, so an unauthenticated call now gets `403` instead of
  reaching the `404`/`409`/`200` it used to reach) - six call sites updated via one shared `_retry()` test
  helper, plus two new regression tests (`test_retry_handover_requires_hub_operator_key`,
  `test_retry_handover_with_wrong_operator_key_returns_403`) mirroring the existing revoke-gate tests.
  74 passed.
- In every environment that doesn't explicitly set `DMS_HUB_OPERATOR_KEY` (including the dev stack today),
  manual handover retry is now fully disabled by default, same as revoke already was - a deliberate
  fail-closed default, not a regression: an operator who wants either action must deliberately configure
  the secret first.
- Live-verified directly against the running container: no `Authorization` header or a wrong one both
  return `403` before reaching the handover lookup (confirmed even against a non-existent handover ID);
  a temporarily-configured correct key passes the gate and reaches the real `404` for that same ID. The
  temporary environment override used only for this verification was reverted immediately afterward -
  the dev stack's default (`hub_operator_key` unset) is unchanged.
