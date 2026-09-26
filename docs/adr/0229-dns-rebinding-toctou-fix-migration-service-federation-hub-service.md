# 0229 — Close the DNS-rebinding TOCTOU window in `migration-service`'s and `federation-hub-service`'s SSRF guards

**Status:** accepted
**Context:** P78-S1 (Phase 78, first session of the tenth gap-analysis round, run after Phase 77 closed).
The round's ADR self-named open-scope sweep re-read `migration-service`'s `_validate_peer_base_url`
(ADR 0182, P59-S5) and `federation-hub-service`'s `_validate_callback_base_url` (ADR 0183, Phase 60
Session 1) and found both guards' own docstrings already honestly named the same residual gap: each
resolves and validates a target hostname exactly once — at pairing-creation time for `migration-service`,
at installation-registration time for `federation-hub-service` — then never looks at it again. Every
subsequent use (every real transfer step for `migration-service`; every handover delivery, including
retries that can fire hours or days later, for `federation-hub-service`) trusts the already-paired
hostname to still resolve somewhere safe. An attacker who controls DNS for an already-legitimately-paired
peer's hostname can point it at a private/loopback/metadata address strictly *after* the one-time check
already passed — a classic DNS-rebinding time-of-check-to-time-of-use (TOCTOU) gap. Both ADRs recorded
this as "an accepted residual gap for a first pass," not a silent oversight — this session closes it.

## Decision

**Re-run the exact same validation function at the actual point of use, on every use, in both services** —
not a new validation mechanism, not a custom IP-pinning transport (considered and rejected, see
"Rationale"), just moving the existing check from "once, early" to "every time, right before the real
call."

- **`migration-service`**: `_make_peer_client(installation)` — the single function that constructs a
  fresh `PeerClient` at all 4 real transfer-step call sites (`push_folder`, `push_document`,
  `push_permission`, `verify`/`release`/`dry_run_check` paths) — now calls `_validate_peer_base_url
  (installation.base_url)` immediately before constructing the client. Since a fresh `PeerClient` is built
  at every one of these call sites already (no caching across a transfer), this closes the window
  completely for every actual peer interaction, not just some of them.
- **`federation-hub-service`**: `_deliver(url, body)` — the single function underlying all 6 delivery
  call sites (immediate delivery + `_run_retry_tick`/`_run_result_retry_tick`'s poll-loop retries, both
  the forward and result legs) — now calls `_validate_callback_base_url(url)` immediately before making
  the actual signed HTTP call. A rejection is caught and treated exactly like any other delivery failure
  (`False` returned, logged as `federation_delivery_rejected_ssrf_guard`) — the existing retry/exhaustion
  status machinery (`pending_retry` → `delivery_failed`) already handles "this target couldn't be
  reached" correctly, so no new handover status or caller-facing behavior change was needed.

Neither service's registration/creation-time check is removed — both checks now run twice (once early,
as a cheap fail-fast for an obviously-bad `base_url`/`callback_base_url` at the point an admin/installation
enters it, and once again immediately before the address is actually used for a real outbound call).

## Rationale

- **Why not a custom IP-pinning transport (resolve once, connect directly to the validated IP, keep the
  original hostname only for the `Host` header/TLS SNI)**: this would close the window even more tightly
  (no second DNS lookup at all between validation and connection, eliminating even a few-hundred-
  millisecond race), but neither service's federation model uses TLS today (`federation-hub-service`'s own
  guard docstring: "this project's federation model has no TLS anywhere, message-level signing via ADR
  0039's certificates instead") and building a custom `httpx` transport is real, fragile new
  infrastructure with no existing precedent anywhere in this codebase. Re-validating immediately before
  use closes the overwhelming majority of the realistic exploitation window (an attacker would need to
  flip DNS in the literal moment between this project's own re-check and `httpx`'s own subsequent internal
  resolution) for a small, low-risk, easily-reviewed change — the right cost/benefit trade-off for this
  finding's actual severity.
- **Why `federation-hub-service`'s fix lives in `_deliver` and not at each of the 6 call sites
  individually**: `_deliver` is already the single choke point every one of them funnels through — adding
  the check there once, rather than duplicating it at 6 call sites, is both less code and structurally
  guarantees no future 7th call site can forget it.
- **Why a rejection returns `False` instead of raising**: `_deliver`'s existing contract already treats
  "delivery failed for any reason" uniformly (network error, non-2xx, now also SSRF-guard rejection) —
  none of its 6 callers expect it to raise, and forcing them to handle a new exception type would be a
  larger, riskier change than reusing the failure path they already handle correctly.
- **Why `migration-service`'s fix lives in `_make_peer_client` and not inside `PeerClient` itself**:
  `PeerClient` is a plain, deliberately dumb `httpx.Client` wrapper (see its own module docstring) with no
  access to `HTTPException`-raising validation semantics or the `installation` row it was built from;
  `_make_peer_client` is the one place in `main.py` that already has both.

## Consequences

- Both services' guard docstrings updated to record the fix and point at each other for context (the two
  guards are historically related, ADR 0183 was explicitly modeled on ADR 0182).
- `docs/services/migration-service.md` and `docs/services/federation-hub-service.md` updated —
  `federation-hub-service.md`'s "Open Points" gets a new struck-through entry (the pattern this doc
  already uses for every other closed gap).
- New tests: `migration-service` +1 (`test_make_peer_client_rejects_target_that_now_resolves_privately`,
  a unit-level test — this service's suite is otherwise 100% black-box HTTP against the real running
  container, which cannot itself simulate DNS rebinding without a test-controlled DNS server this
  codebase doesn't have; the test monkeypatches `socket.getaddrinfo` and calls `_make_peer_client`
  directly, importable the same way `conftest.py` already imports `migration_service.models`), 19/19
  total. `federation-hub-service` +1
  (`test_deliver_rejects_target_that_rebinds_to_a_private_address`, this service's suite already uses
  in-process `TestClient(app)` so the module is directly importable — this test simulates a *genuine*
  rebinding sequence: the callback hostname resolves to a real public address at registration [passes],
  then to a private address by the time `_deliver` tries to reach it [rejected], and asserts the mocked
  transport is never even reached), 90/90 total.
- Both services rebuilt and redeployed; live-verified against the real running stack: `migration-service`
  still correctly rejects a direct private `base_url` at pairing-creation time and still accepts a real
  self-loopback pairing (both existing behaviors unchanged); `federation-hub-service` still correctly
  rejects a direct private `callback_base_url` at registration time. The new re-validation-at-use-time
  behavior itself is exhaustively covered by the two new unit tests above (a live DNS-rebinding
  reproduction would need a real, test-controlled second DNS record this environment doesn't have).
