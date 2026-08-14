# 0084 — fleet-management-service & license-service: key rotation

**Status:** accepted (Session 1 of 4, see Phase 21 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap Phase 21 Session 1, affects `fleet-management-service` and `license-service`

## Decision

The plan names `workflow-service`'s already existing `POST /federation/rotate-key` (ADR 0039) as the
model: "a new key is issued, the old one stays valid briefly for a transition, then is invalidated." A
closer look at this model revealed: there is actually **no time-based transition window** —
`federation-hub-service.repository.rotate_installation_key` replaces `Installation.public_key_pem`
immediately and atomically as soon as the rotation request (signed with the still-current key) has been
successfully verified. "The old one stays valid briefly" means there: the old key remains the only
valid one until the rotation call goes through, after which only the new one is valid — no period
during which both would be accepted simultaneously. This session replicates this pattern for
`fleet-management-service`, where it fits structurally the same way, and develops a differing but
conceptually fitting variant for `license-service`, since no self-generated signing key that could be
"rotated" exists there technically.

### fleet-management-service

New endpoint `POST /installations/{id}/rotate-key` — replaces `ManagedInstallation.fleet_agent_api_key`
immediately and atomically (`repository.rotate_managed_installation_key`), optionally with an
operator-supplied value (same flexibility as the existing `POST /installations`), otherwise a new value
is generated. Response schema identical to initial creation (`ManagedInstallationCreateOut`) — the
plaintext key is only ever returned in this one response.

### license-service

`fleet_agent_api_key` on `fleet-management-service` is a self-generated, self-managed key —
`license-service`'s "signing key" is something fundamentally different: the private key used to sign
license files belongs to the **licensor** and, per explicit ADR-0032 requirement, **never** resides in
this repository/deployment ("must not appear anywhere in the repository"). `license-service` only
holds the public **verification key** (`settings.license_public_key_pem`) — there is no
self-generated key here that this service could rotate. A 1:1 transfer of `fleet-management-service`'s
pattern is therefore not applicable.

Instead: a new, optional setting `license_previous_public_key_pem` (default `None`). When the licensor
switches their key pair, the operator configures `license_public_key_pem` to the NEW public key and
`license_previous_public_key_pem` to the OLD one. `license_verifier.decode()` first tries
`public_key_pem`, and on failure the optional `previous_public_key_pem` — **here a real transition
window actually exists** (unlike the other two rotation patterns in this project, ADR 0039/
fleet-management-service above): already installed licenses signed under the old key remain valid on
every renewed status check (`GET /license/status`, which re-reads `raw_token`), while newly issued
licenses may already be signed with the new key. The operator resets
`license_previous_public_key_pem` back to `None` after the transition period concludes ("then
invalidated").

## Rationale

- **Why `fleet-management-service` has NO transition window, even though `workflow-service`'s model
  has one**: the trust direction structurally differs. For hub installation rotation, the TARGET SIDE
  (the hub) actively confirms the rotation via an HTTP call and can therefore switch over at the exact
  moment it receives the new key. For `fleet_agent_api_key` there is no analogous feedback loop:
  `fleet-management-service` only PRESENTS the key; the target installation verifies it against a value
  **read statically from an env var at its own startup** (`DMS_FLEET_AGENT_API_KEY`) — there is no
  channel through which `fleet-management-service` could change this value on the target installation
  at runtime. A "transition window" simply could not be technically represented here; rotation
  unavoidably remains a two-step, partially manual process (see "Consequences").
- **Why `license-service`'s solution is NOT implemented as "key rotation" in the literal sense**: there
  is no key belonging to this service that could be rotated — only an externally issued trust anchor
  that gets swapped out. The solution carries over the core idea ("old state stays valid briefly, then
  invalidated") to what actually exists here: the public verification key.
- **Why no `models.py`/DB table for `license-service`'s previous key**: for its core task (license
  verification), the service only has settings anyway, no signing-key record (unlike
  `federation-hub-service`'s `HubIdentity`/`signature-service`'s `InternalCa`) — a single optional
  configuration value suffices, no new persistence layer needed.

## Consequences

- **`fleet-management-service`: rotation remains a two-step, partially manual process** — documented in
  the endpoint docstring and in `docs/services/fleet-management-service.md`: after
  `POST .../rotate-key`, the new value is only active on the `fleet-management-service` side; until an
  operator manually switches the target installation to `DMS_FLEET_AGENT_API_KEY=<new value>` and
  restarts it, outgoing calls (`GET .../status`, `POST .../license`, `POST .../provision`) fail with
  `401`/`403` — a deliberately accepted, honestly documented state instead of a seemingly automatic but
  actually non-functional full-circle mechanism. The optional `fleet_agent_api_key` request parameter
  allows the reverse, recommended order (switch the installation first, then follow up here), which
  practically avoids this failure gap.
- **Migration**: none needed — both changes are additive (a new endpoint and a new optional setting
  with a backward-compatible default of `None`).
- **Tests**: `fleet-management-service` 30 (previously 26, +4: default generation, operator-supplied
  value, actual use of the new value on an outgoing call, `404` for an unknown installation).
  `license-service` 37 (previously 32, +5, all in `test_license_verifier.py`: fallback to the previous
  key during the transition period, preference for the current key without needing fallback, failure
  when neither current nor previous key matches, unchanged behavior with no previous key configured).
- Docs: `docs/services/fleet-management-service.md`/`license-service.md` ("Open Points" marked as
  resolved, new endpoint/setting documentation).
- **Verified live against the real running stack** (image rebuild + restart of both services):
  `fleet-management-service` — a real installation registered, `POST .../rotate-key` confirmed both
  with an automatically generated and with an operator-supplied value (each with an actually changed
  return value), `404` for an unknown installation confirmed. `license-service` — reproduced for real
  via a temporary compose override file (`DMS_LICENSE_PUBLIC_KEY_PEM` set to a freshly generated,
  independent key, `DMS_LICENSE_PREVIOUS_PUBLIC_KEY_PEM` set to the previous default key): a real
  license already installed under the old key before this session remained valid via
  `GET /license/status` (fallback path actually exercised, no mocking); as a counter-check with no
  previous key configured, the same license was correctly reported as `valid=false`/
  `"Lizenzsignatur ungueltig"` — confirming that the fallback performs real verification work rather
  than opening a gap. Reset to the original configuration after the test (`valid=true` confirmed
  again).
