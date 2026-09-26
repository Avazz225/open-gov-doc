# 0232 — Federation self-registration vs. the callback SSRF guard: the real root cause of the "flake"

**Status:** accepted
**Context:** P79-S3 (Phase 79, third session — test-suite health cleanup). ADR 0195 first observed
`auth-service`/`workflow-service` federation tests intermittently failing (`test_dispatch_records_delivery_failed_for_unreachable_target`
on one run, `test_dispatch_builds_xdomea_package_instead_of_raw_task_data` on another), confirmed
"unrelated to that session's changes" by reverting them entirely and reproducing the same failure class
on unmodified code, but never root-caused it. ADR 0211 reconfirmed the same alternation later, again
without a root cause. Both treated it as an intermittent ordering/timing flake.

Investigating it directly for this session's "test-suite health" scope found a **single, deterministic**
cause, not a race:

- `auth_service`/`workflow_service`'s `_ensure_federation_identity` self-registers with the real,
  running `federation-hub-service` at startup, using
  `callback_base_url = f"{settings.installation_gateway_base_url}/api/<service>"`, where
  `installation_gateway_base_url` defaults to `"http://gateway-service:8000"` — this project's own
  Docker-Compose-internal hostname for its own gateway.
- **Phase 60's** `_validate_callback_base_url` SSRF guard (added well after this default was chosen)
  resolves that hostname and rejects it with `422` whenever it resolves to a private/loopback/link-local
  address — which `gateway-service` (a Compose-internal service name) always does, from
  `federation-hub-service`'s own point of view (same Docker network).
- The registration call's `except httpx.HTTPError` swallows this `422` and leaves
  `app.state.federation_hub_client`/`federation_client` as `None`. Every federation-dependent test then
  fails in whatever shape depends on that being set — three signature-check tests and one key-rotation
  test in `workflow-service` expect `503` explicitly when it's unset; `_dispatch_pending_federation_tasks`
  unconditionally skips (no `FederationTask` row, no xdomea package built) when it's `None`; four of
  `auth-service`'s `test_directory.py` tests need a real `FederationIdentity` row that never gets created.
- `workflow-service`'s `conftest.py` sets `DMS_FEDERATION_HUB_BASE_URL` **unconditionally at import time**
  (federation is always "enabled" for every test in that file), so this failure is not intermittent there
  at all — reproduced deterministically, 6/6 of the previously-flagged tests failing together in the same
  run. `auth-service`'s equivalent (`federation_enabled`, a per-test opt-in fixture) only runs for the
  handful of tests that use it, which is why its own manifestation looked comparatively rarer and
  "alternating" across the two ADRs' different observation windows — not because the cause itself varies,
  but because which tests exercise it differs run to run depending on unrelated test selection/ordering.

## Decision

Fix the tests, not the guard: override `installation_gateway_base_url` to a genuinely public,
non-reserved address (`8.8.8.8`, already this project's own established placeholder for "a real, public,
non-reserved IP" from P78-S1's own SSRF-guard tests) in exactly the two places that self-register for
real — `auth-service`'s `federation_enabled` fixture (`monkeypatch.setattr`) and `workflow-service`'s
`conftest.py` (`os.environ["DMS_INSTALLATION_GATEWAY_BASE_URL"]`, set at the same import-time point the
federation-hub URL itself already is). This passes the SSRF guard (a real public IP is not private/
loopback/link-local) without weakening it, and without needing any real delivery to succeed against that
address — none of the affected tests exercise an actual inbound callback to this installation's own
address, only the registration handshake itself.

**Not fixed**: the underlying architectural fact that self-registration is inherently non-functional for
genuine cross-installation delivery in a single-Docker-Compose-network deployment (this whole dev/demo
stack lives on one private network by construction, so ANY same-stack hostname/IP used as a "self"
address is, correctly, indistinguishable from an SSRF target). This is not a bug: a real production
deployment configures `INSTALLATION_GATEWAY_BASE_URL` to that installation's actual externally-reachable
address, at which point registration succeeds for real, exactly as `_ensure_federation_identity`'s own
graceful degradation (`federation_hub_initial_registration_failed`, contact search stays disabled, "no
hard dependency of this installation") already anticipates. The single-stack dev/demo environment simply
has no real second installation to usefully federate with anyway.

## Rationale

- **Why not add an SSRF-guard allowlist/exception for the caller's own address instead**: verifying that a
  registration request's `callback_base_url` genuinely belongs to the calling installation (rather than an
  attacker naming an arbitrary private target) needs its own real mechanism (e.g. a challenge-response
  callback), which is a materially larger, separate security-design question — clearly out of scope for a
  "test-suite health cleanup" session, and the tests don't need it: they only need registration to
  *succeed*, not to prove anything about the address's authenticity.
- **Why the two ADRs missed this for so long**: both treated the failure as intermittent because the
  *symptom* (which specific assertion trips) does vary with test ordering/selection, even though the
  *cause* (the `422` at every self-registration attempt) does not — investigating the actual HTTP
  exchange (rather than re-running the suite and comparing which test failed) was what surfaced it.

## Consequences

- `services/auth-service/tests/test_directory.py`: `federation_enabled` fixture now also monkeypatches
  `installation_gateway_base_url` to `http://8.8.8.8:8000`.
- `services/workflow-service/tests/conftest.py`: sets `DMS_INSTALLATION_GATEWAY_BASE_URL` to the same
  value, at the same import-time point `DMS_FEDERATION_HUB_BASE_URL` is already set.
- `auth-service` full suite: 158/162 → 162/162. `workflow-service` full suite: 232/238 → 238/238.
- No production code changed, no change to the SSRF guard's own behavior (ADR 0229/`_validate_callback_base_url`
  unaffected) — this was purely a test-fixture gap once the real cause was understood.
- ADR 0195/0211 are not rewritten (this project's own standing convention) — this ADR supersedes their
  "intermittent flake, cause unknown" framing with the actual, deterministic cause.
