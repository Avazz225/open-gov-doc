# 0212 — fleet-management-service: per-operator authentication scoping

**Status:** accepted
**Context:** P73-S2 (Phase 73, ninth gap-analysis round). A **scoping-only** session, explicitly no
implementation commitment — matches this project's own established pattern for open-ended architectural
questions (ADR 0147/0208/0209). Re-examines ADR 0038's four-eyes approval flow
(`POST /rollouts/{id}/installations/{iid}/approve`) in light of a user request for "real per-user auth",
and verifies first whether a lighter mechanism than a full Keycloak login flow already has a reusable
precedent in this codebase.

## What already exists (verified against the real code, not assumed)

- **`services/fleet-management-service/src/fleet_management_service/main.py`'s `_require_operator_key`
  (~line 92-108) is the service's ONLY authentication primitive**, gating every endpoint except
  `/healthz` (ADR 0172) via a single shared `Authorization: Bearer <settings.fleet_operator_key>`,
  compared with `hmac.compare_digest` (timing-safe since P67-S1/ADR 0197). It returns `None` — pass or
  `403`, no notion of *who* authenticated, only *whether* the right shared secret was presented.
- **`approve_installation_run` (~line 642-672) checks only `payload.actor == run.proposed_by`.** Both
  `proposed_by` (set by `mark_installation_run_done`, ~line 596-639, from `payload.actor` when a
  `requires_approval` gate step is proposed) and `approve`'s own `actor` are plain `str` fields on
  `MarkDoneRequest`/`ApprovalDecision` (`schemas.py` line 152-160) — free text from the request body, never
  validated against any identity. `RolloutStart.started_by` is the same shape. Confirmed: nothing server-side
  stops one caller from proposing as `"alice"` and approving the same run as `"alice2"`.
- **ADR 0038 already named this exact residual deliberately**, not as an oversight: "a real protection …
  no protection against one person with two aliases" — the two-call structural flow was chosen specifically
  *because* the service has no user management of its own (isolation 3a: it structurally must never hold a
  valid principal for any single installation's realm).
- **The blast radius of a wrong/malicious approval is real but bounded by ADR 0038's other decision**: a
  `gate` step's actual execution (scope lock, maintenance mode, rolling update, backup, final release)
  happens entirely outside this service — `fleet-management-service` never remote-controls it, only
  records that it happened. So a spoofed approval cannot itself trigger a destructive action on a target
  installation; it forges the recorded "two people signed off" trust signal that whoever performs the next
  gate step, or a later audit, relies on. That is still a meaningful integrity gap for a rollout process
  explicitly designed around four-eyes, not a cosmetic one.
- **`fleet-management-service` structurally cannot sit behind `gateway-service`, and therefore cannot get
  `X-DMS-Principal`.** `docs/services/fleet-management-service.md`'s "Self-Registration: None — this
  service does not belong to any single installation" is not incidental: `gateway-service` proxies to
  services it discovers via `registry-service`, and `X-DMS-Principal`/`-Roles` are only ever set for a
  route resolved that way, minted from a JWT issued by that ONE installation's own Keycloak realm
  (`docs/services/gateway-service.md`, `docs/services/auth-service.md` "Realm/Client Bootstrap": one `dms`
  realm, bootstrapped per installation). Reusing any existing installation's realm/gateway here would mean
  a fleet operator authenticates as a principal of ONE specific installation — precisely what ADR 0038's
  isolation-3a reasoning forbids. Same reasoning already applies, and was already accepted, for
  `federation-hub-service` (also unregistered, also gated only by a shared `hub_operator_key`).
- **Today's only client of this service's write endpoints is `curl`/operator-CLI.** `admin-ui`'s
  `FleetManagementView` (P67-S1, ADR 0197) covers only installation registration/listing/deletion/status/
  license-push — groups, plans, and rollouts (where `mark-done`/`approve`/`reject`/`start` all live) were
  explicitly left out as "a separate, larger UI surface, not built in this session," still not built as of
  this session. The existing view's own operator-key handling ("typed by a human operator per session, kept
  only in component state, never persisted") confirms the intended usage model: no login, a secret entered
  ad hoc. There is no `apps/*fleet*` app and no browser flow anywhere that calls `approve`/`mark-done`.
- **Two existing precedents for "one credential per distinct caller identity, not one shared secret"
  already exist in this codebase, both for machine/installation counterparties, not human operators:**
  - `federation-hub-service` (ADR 0039/0085): each installation registers its own RSA keypair,
    authenticates every write with `X-Installation-Signature`, later extended with a full X.509 certificate
    layer. Real, but heavyweight — asymmetric crypto plus a small internal CA, built because installations
    already needed a keypair for end-to-end encryption anyway. Fleet operators (humans/CI) have no
    equivalent pre-existing keypair need.
  - `migration-service`'s `POST /paired-installations` (ADR 0182): a single opaque `api_key` generated
    per paired installation, shown once, presented as `Authorization: Bearer <api_key>` — the same shape
    as the one shared `fleet_operator_key` today, just already forked into one row per counterparty instead
    of one global value. This is the closer, lighter precedent.
  - `auth-service`'s `TechnicalAccount` (ADR 0063/0065) additionally shows this project already storing a
    hashed credential (bcrypt) per distinct *named* local identity, outside Keycloak entirely, when a
    service needs individual attribution without full IAM.
  - Checked and ruled out as *not* applicable: `webdav-connector`/`cmis-connector` reuse `auth-service`'s
    `POST /login` — but both are deployed inside one installation and authenticate real installation users,
    the same isolation-3a objection as the gateway/`X-DMS-Principal` option above.
  - `permission-service`'s domain-admin roles (ADR 0023, `docs/services/permission-service.md`) are likewise
    installation-local RBAC — no existing DMS admin persona can double as a fleet operator without the same
    cross-realm-trust violation.

## Decision

**Recommend replacing the single shared `fleet_operator_key` with named, individually-revocable
per-operator bearer tokens — not a Keycloak realm/login flow.** This is the `migration-service`
`paired-installations` pattern (one generated secret per distinct identified counterparty), applied to
human/CI fleet operators instead of paired installations, and is the lightest mechanism that actually
closes the real gap (two distinct, attributable secrets instead of two typed strings from the same secret).

Concretely, for the future build session:

1. **New `fleet.fleet_operator` table**: `id` (PK), `name` (unique, human-readable — the identity that ends
   up in `proposed_by`/`actor`), `token_hash`, `created_at`, `revoked_at` (nullable). Store
   `hashlib.sha256(token).hexdigest()`, not `bcrypt` — these are high-entropy generated opaque tokens (like
   `federation-hub-service`'s/`migration-service`'s own generated `api_key`s), not low-entropy human
   passwords; a slow, salted password hash buys nothing here and would be this service's first new
   dependency (`bcrypt` is not currently in `fleet-management-service/pyproject.toml`) for no real benefit.
   Plaintext token returned exactly once at creation — the same "shown once" convention this service
   already uses for `fleet_agent_api_key`.
2. **`_require_operator_key` becomes an identity-resolving dependency** (`_require_fleet_operator`,
   returning the matched `FleetOperator.name` instead of `None`), checked via `hmac.compare_digest` against
   each active row's `token_hash` (small N, same linear-scan-over-active-secrets shape `migration-service`
   already accepts for `paired-installations`). Every existing `dependencies=[Depends(_require_operator_key)]`
   site keeps working unchanged in shape — only the handful of endpoints that currently read an identity
   from the request body change what they inject.
3. **`mark-done`/`approve`/`reject`/`start` derive `proposed_by`/`actor`/`started_by` from the resolved
   operator name, not from `payload.actor`/`payload.started_by`.** Drop those fields from
   `MarkDoneRequest`/`ApprovalDecision`/`RolloutStart` outright rather than keeping them as an
   unenforced/ignored leftover — the same "the verified channel, never the request body, is the identity of
   record" resolution `workflow-service`'s `_require_claim_authorization_if_claimed` (ADR 0211) already
   reached for the equivalent body-vs-header trust question.
4. **The existing `actor != proposed_by` check in `approve_installation_run` (~line 665) stays exactly as
   is — this proposal wraps it, does not replace it.** Only where the two compared strings come from
   changes: today both are attacker-controlled free text; after this change both are names resolved
   server-side from whichever token authenticated each of the two separate calls. The four-eyes structure
   ADR 0038 chose (two separate API calls, not a single cryptographic two-signature primitive) is unchanged.
5. **Bootstrap migration for the existing shared secret**: on startup, idempotently ensure one
   `FleetOperator` row named e.g. `"bootstrap"` whose `token_hash` matches the current
   `settings.fleet_operator_key`, if configured — every existing deployment keeps working with zero
   operational change on day one. A real fleet operator then mints additional named tokens for each
   distinct human/CI identity via a new `POST /operators` endpoint (gated by an already-valid operator
   token — same "an existing credential mints the next ones" bootstrap shape `auth-service`'s
   superuser/domain-admin accounts already established), plus `GET /operators` (names only, never token
   hashes) and a revoke endpoint.
6. **No UI is in scope, deliberately deferred.** Nothing in this service's write surface is called from a
   browser today (`approve`/`mark-done`/`reject`/`start` have zero UI callers, confirmed above) — minting a
   token is a one-time CLI/`curl` action per operator, exactly matching how `fleet_operator_key` is obtained
   and used today. If/when the still-unbuilt groups/plans/rollouts console (flagged as future work in ADR
   0197) gets built, it would naturally prompt for a named token instead of the shared key, reusing
   `FleetManagementView`'s existing "typed per session, kept only in component state" convention — but that
   console is a separate, larger, not-yet-scheduled piece of work and is not a prerequisite for closing
   today's spoofing gap.

**Scope/complexity estimate: small.** One new table, one dependency rewrite (still a single header check,
just resolving to an identity instead of a boolean), three schema fields dropped, one idempotent bootstrap
migration, three new endpoints (`POST`/`GET`/revoke `/operators`, themselves gated by the now-identity-aware
dependency), roughly 6-10 new tests (operator CRUD, propose/approve with two distinct real tokens succeeding,
propose/approve with the SAME token now correctly rejected even under two different body-supplied names,
bootstrap-row idempotency). No new service, no new UI, no new external dependency, no Keycloak.

## Rationale

- **Why named tokens over a Keycloak realm/login flow**: this project's own repeated, demonstrated
  preference is reusing an existing mechanism over inventing a new one, and picking the smallest slice that
  closes the actual, currently-named gap rather than the largest plausible one (ADR 0209/0208's identical
  judgment). A dedicated `fleet-operators` Keycloak realm would be genuinely new infrastructure — not reuse
  of anything, since every existing realm is deliberately installation-scoped (isolation 3a) and this
  service structurally sits outside all of them (no self-registration, no gateway proxy). Standing it up
  would mean deploying a second, standalone Keycloak instance, building JWT verification directly into
  `fleet-management-service` (it cannot inherit the gateway's, having none), and — since nothing today
  drives a browser to this service's write surface — almost certainly a login UI too, to make a JWT
  reachable via anything other than a hand-rolled password-grant `curl` call. That is a materially larger
  build than the actual, named threat (two free-text aliases from the same caller) requires, and named
  tokens already have two live precedents in this exact codebase for the identical "per distinct
  counterparty, not one shared secret" problem shape.
- **Why not the federation-hub-service signature/certificate pattern**: that model exists because
  installations already need an asymmetric keypair for end-to-end encryption independent of authentication
  — reusing it there is free. Fleet operators have no equivalent pre-existing cryptographic material; a
  keypair-per-human-operator would be new complexity invented specifically for this problem, where an
  opaque generated bearer token (the `migration-service`/current-`fleet_operator_key` shape, just forked
  per identity) already suffices for the threat being closed.
- **Why `hashlib.sha256` over `bcrypt`**: these tokens are machine-generated, high-entropy secrets, not
  human-chosen passwords — the threat a slow hash defends against (offline dictionary/brute-force attack on
  a low-entropy secret) does not apply, and adding `bcrypt` would be this service's first new dependency for
  no corresponding security gain. Keeps the "reuse what's already minimal" footprint this service has had
  since P13-S2.
- **Honest boundary of what this actually closes**: it closes ADR 0038's own named residual — one caller
  typing two different free-text aliases into the same request body no longer defeats the check, because
  the two aliases must now correspond to two tokens that were each deliberately minted and kept separately.
  It does **not** defend against a single compromised caller who has obtained (stolen, or was handed) two
  DIFFERENT operators' real tokens — that is a credential-custody problem, categorically outside what "stop
  typing two names into a form" was ever meant to solve, and not the threat ADR 0038 flagged. Documented
  here explicitly so a future round doesn't mistake this for full identity assurance.
- **Why the bootstrap-from-env migration, not a breaking cutover**: every other credential-shape change in
  this project's history (`hub_operator_key`/`fleet_operator_key` themselves, ADR 0172) defaulted to
  fail-closed for NEW deployments while explicitly not breaking already-configured ones on the same change;
  the same courtesy applies here — an operator who never bothers minting named tokens keeps working exactly
  as today, under a single `"bootstrap"` identity (which then also becomes the value baked into
  `proposed_by`/`actor` for that caller — still real, just coarse, an acceptable default rather than a
  forced migration).

## Consequences

- **Not built in this session** — this is scoping only, per the plan's own framing. A future build session
  can start directly from this ADR: table shape, dependency rewrite, bootstrap migration, and the exact
  three schema fields to drop are all decided here, not left open.
- **What a future session still needs to decide, not resolved here**: token format/length (a UUID4 is
  probably sufficient, matching `fleet_agent_api_key`'s own generation, but this wasn't pinned down), and
  whether `/operators` itself should be reachable only by the bootstrap identity or by any already-valid
  operator (this ADR assumes the latter — any valid operator can mint another — but flags it as worth a
  second look at build time given it means a single compromised token can still mint arbitrary new ones).
- **`docs/services/fleet-management-service.md`'s "Open Points" bullet "Four-eyes principle without its own
  user management" (ADR 0038) becomes partially closed** once this is built — the comparison stops being a
  plain-text coincidence and starts being between two deliberately-distinct, individually revocable
  secrets. The bullet should be updated to note the remaining, now much narrower residual (shared-token
  custody, not aliasing) rather than removed outright.
- **Revisit this decision (move to a real Keycloak-backed model) only if**: (a) `fleet-management-service`
  ever gains genuine cross-installation infrastructure of its own (a dedicated realm + gateway-equivalent),
  making JWT verification no longer a from-scratch build; (b) the deferred groups/plans/rollouts console
  actually gets built and product requirements demand session-based login UX beyond "paste a token"; or
  (c) a real incident or near-miss is traced specifically to token custody (shared/leaked tokens), not to
  the aliasing gap this ADR closes — at that point stronger per-operator assurance (short-lived tokens,
  mTLS, or a real OIDC flow) becomes worth its materially larger cost. Not worth building speculatively
  before any of these three actually occurs.
