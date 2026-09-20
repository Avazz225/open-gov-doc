# 0172 — Gating all of `fleet-management-service` via a new `fleet_operator_key`

**Status:** accepted
**Context:** P54-S1 (Phase 54, "Critical Authorization Bugs" — the first of the two new gap-analysis
rounds planned after Phase 53). Found during this round's dedicated live-code security sweep (not a doc
sweep — nothing had previously flagged this): `fleet-management-service`'s entire API has **zero
authentication of any kind**. Every endpoint — `create_installation`, `delete_installation`,
`rotate_installation_key`, `push_license`, `provision_installation`, the group/plan/rollout CRUD, and
every rollout step-advance/approve/reject/retry endpoint — has no `Depends()`-based auth, header check, or
API-key gate, confirmed by grepping the whole file for `Depends|Header|role|permission|capability` and
finding only `Depends(get_session)`.

This is materially worse than the one gap ADR 0162 found and fixed in `federation-hub-service` (a single
ungated mutating endpoint, `POST /handovers/{id}/retry`): here, `rotate_installation_key` mints and
returns the plaintext `fleet_agent_api_key` a real managed installation's `config-service`/`license-service`
trusts (see `docs/services/config-service.md`'s/`license-service.md`'s own `_is_fleet_agent` checks), and
even a plain `GET /installations` was never going to leak that key only because the response model happens
to omit it (`ManagedInstallationOut` deliberately excludes `fleet_agent_api_key` — a coincidental mitigation,
not a real access control). Anything with network reach to this service (any other pod/compromised
container, or a dev/compose deployment with the port mapped) can today delete managed installations,
harvest a fresh plaintext agent key via rotate-key, push arbitrary licenses/config to any managed
installation, and force-advance/approve/reject fleet-wide rollout steps.

## Decision

**Gate every endpoint except `/healthz` behind a new, fail-closed `settings.fleet_operator_key` bearer
token — the same mechanism ADR 0162 already established for `federation-hub-service`'s
`hub_operator_key`, applied to the whole API surface rather than one endpoint.** A single shared
`_require_operator_key` dependency function, added via `dependencies=[Depends(_require_operator_key)]`
on every route. With no `fleet_operator_key` configured (`None` by default, including the dev stack), the
entire API is fully locked — an operator must deliberately configure the secret before this service is
usable at all, exactly like `federation-hub-service`'s revoke/retry endpoints already are.

## Rationale

- **Why reuse the `hub_operator_key` mechanism rather than a Keycloak/`permission-service` model**: per
  its own `Settings` docstring, `fleet-management-service` is deliberately **not** an internal service of
  any one installation — it has no Keycloak realm, no registry-service registration, no event bus, the
  same "independently operated, outside any single installation's trust boundary" shape as
  `federation-hub-service` (ADR 0028/0038). It has no admin-JWT/capability model to plug into, and
  building one from scratch for this one service would be a materially larger, architecturally
  inconsistent change for what is fundamentally the same kind of gap ADR 0162 already solved elsewhere in
  this project — the same "bounded fix over full redesign" principle applied again.
- **Why gate the ENTIRE API, not just the obviously-mutating endpoints (unlike ADR 0162's single-endpoint
  scope)**: every endpoint here is sensitive by this service's own nature — `GET /installations` still
  enumerates every managed installation's `gateway_base_url` and display name (reconnaissance value even
  without the key), `GET /installations/{id}/status` reaches out live to a real managed installation's
  gateway, and `GET /plans`/`GET /rollouts` expose the fleet's entire update-rollout state and history. Ad
  hoc classification of "this one is safe to leave open" invites exactly the kind of silent gap this
  session exists to close — a single, uniform gate is simpler to reason about and audit than 20 individual
  judgment calls, especially for a service with no legitimate anonymous caller in the first place (unlike
  `federation-hub-service`, which does have deliberately-ungated pure-metadata reads used by peer
  installations, `GET /installations`/`GET /handovers`).
- **Why NOT distinguish "console/admin caller" from "the managed installation's own agent" with two
  separate keys**: confirmed by tracing the actual call graph — no managed installation ever calls INTO
  `fleet-management-service`. The `fleet_agent_api_key` this service mints is used by `config-service`'s/
  `license-service`'s own `_is_fleet_agent` checks to verify calls arriving FROM this service, never the
  reverse. `fleet-management-service`'s entire inbound surface is operator/console-only, exactly like
  `federation-hub-service`'s revoke/retry endpoints — one secret is enough, a second would only fragment
  the convention without buying real additional safety.
- **Why not touch `approve_installation_run`'s `payload.actor` four-eyes check**: its own docstring
  already documents, as an accepted ADR 0038 design boundary, that the proposer/approver separation here is
  structural (two separate calls) rather than cryptographically bound to two real identities, since this
  service manages no users of its own. That limitation is unrelated to and unaffected by this session — a
  caller now additionally needs the operator key to reach either call at all, which meaningfully raises the
  bar even though `actor` itself remains a free-text field.

## Consequences

- Every one of `fleet-management-service`'s ~20 non-`/healthz` endpoints now requires
  `Authorization: Bearer <fleet_operator_key>`, and the service is **fully locked in every environment
  that doesn't explicitly configure `DMS_FLEET_OPERATOR_KEY`** (including the dev stack today, unchanged
  from `federation-hub-service`'s equivalent default) — a deliberate fail-closed default, not a
  regression.
- Every existing test needed a shared `_headers()`/fixture helper adding the operator-key header to every
  call site, plus new regression tests proving both "no header" and "wrong key" are rejected with `403`
  before reaching any business logic (mirroring ADR 0162's own `test_retry_handover_requires_hub_operator_key`/
  `test_retry_handover_with_wrong_operator_key_returns_403` pattern).
- No admin-UI currently calls this service (`apps/admin-ui/src/lib/api.ts` has no `fleet-management-service`
  call site at all — confirmed via grep) — this remains a pure `curl`/operator-CLI-driven service, so there
  is no frontend impacted by this change, unlike ADR 0162's `admin-ui` operator-key input field.
- Live-verified directly against the running container: every previously-open endpoint now returns `403`
  with no `Authorization` header and with a wrong one; a temporarily-configured correct key passes the
  gate and reaches the real business-logic response. The temporary environment override used only for
  verification was reverted immediately afterward — the dev stack's default (`fleet_operator_key` unset)
  is unchanged.
