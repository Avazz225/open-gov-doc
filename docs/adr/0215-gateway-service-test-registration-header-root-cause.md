# 0215 — gateway-service: root-caused the 3 recurring test failures, fixed

**Status:** accepted
**Context:** P73-S5 (Phase 73, ninth and final gap-analysis-round session of this phase). Three
`gateway-service` tests (`test_valid_token_routes_via_registry_to_real_instance`,
`test_client_supplied_x_dms_principal_header_is_overridden_by_gateway`,
`test_draining_instance_is_excluded_from_routing`) have shown up as "pre-existing, unrelated" failures in
this project's full-regression runs repeatedly since at least Phase 60, re-noted (never diagnosed) across
many sessions' `PROGRESS.md` entries. [ADR 0200](0200-p66s2-business-key-validation-regression-correction.md)
(P68-S1) got the closest: it identified the actual symptom ("`401` from a real `registry-service` call")
but explicitly stopped there — "found but not fully root-caused; also flagged, not fixed." This session's
job was to actually finish that trace.

## Root cause

All three tests share one setup helper, `_register_instance()` (`services/gateway-service/tests/test_api.py`),
which calls the real, separately-running `registry-service` container's `POST /instances` with **no
`X-DMS-Principal` header at all**. That endpoint has required a real `X-DMS-Principal` matching the
registered `service_type` since **Phase 59 Session 4** — a genuine, deliberate security fix (self-
registration only, closing a traffic-hijack vector where any caller could register a fake instance of an
arbitrary `service_type` at an attacker-controlled address). Every real self-registering service in this
codebase gets this header for free from `dms_registry_client.RegistryRegistration`. This test helper is a
hand-rolled bypass of that shared library, written before Phase 59 Session 4 existed, and was simply never
updated when the gate went in — so it has been failing with `401` on every single regression run since,
for a service (gateway-service) that has nothing at all to do with the actual registration gate.

Fixing just that unblocked the first two tests. The third (`test_draining_instance_is_excluded_from_routing`)
uncovered a second, independent instance of the identical class of bug one call deeper: its own
`_drain_instance()` helper calls `POST /instances/{id}/drain` with no `Authorization` header, and that
endpoint has required a bearer `registry_operator_key` since the same Phase 59 Session 4 session — deliberately
**unconfigured** (permanently `403`) unless an operator explicitly opts in ("a registry operator must
deliberately enable drain/rollback," per the endpoint's own docstring). This project's bundled dev/test
stack (`infra/docker-compose.yml`) never set this key for any service, so `/drain` was unreachable in this
dev environment at all, by anyone, until this session.

## Decision

**Fixed, not merely documented** (the plan's DoD allowed either outcome; the root cause turned out
tractable):

1. `_register_instance()`/`_deregister_instance()` now send `X-DMS-Principal: <service_type>`, matching
   `registry-service`'s own self-registration contract exactly.
2. `infra/docker-compose.yml`'s `registry-service` block gains `DMS_REGISTRY_OPERATOR_KEY`, defaulted to a
   fixed, documented dev-only value (`registry_operator_dev_only`, overridable via `REGISTRY_OPERATOR_KEY`
   in a real installation's own environment, same `${VAR:-dev_default}` convention this compose file
   already uses for Postgres passwords) — the code's own default (fully locked, unset) is preserved for
   every environment that doesn't explicitly set this dev-stack default, so production posture is
   unchanged.
3. `_drain_instance()` sends `Authorization: Bearer <the same key>`, read via a new
   `TEST_REGISTRY_OPERATOR_KEY` env var (same naming convention as this file's existing
   `TEST_REGISTRY_SERVICE_URL` etc.).

## Rationale

- **Why this recurred across six-plus ADRs without ever being traced**: every one of those sessions was
  running the FULL regression suite for an unrelated reason, saw the same 3 names in the failure list,
  correctly recognized them as unrelated to whatever THAT session was about, and moved on — a reasonable
  call in isolation, repeated enough times that "recurring, pre-existing, unrelated" became its own kind
  of camouflage. ADR 0200 is proof the failure mode was visible to a careful look; what was missing was a
  session whose actual job was to stop and trace it, which is what P73-S5 explicitly was.
- **Why a fixed dev-stack default for `REGISTRY_OPERATOR_KEY` doesn't weaken the Phase 59 Session 4
  security posture**: that gate's whole point is that a REAL installation must deliberately opt in — this
  compose file is expressly the bundled dev/test stack (same reasoning already documented next to it for
  `DMS_LICENSABLE_COMPONENTS`'s own deliberately-non-production default), not a production deployment
  template, and the underlying code default (unset, fully locked) is completely unchanged for any
  environment that doesn't source this specific file.
- **Why fix rather than mark `xfail`**: an `xfail` would have re-hidden a now fully understood, trivially
  fixable gap behind a different kind of camouflage — the plan's own framing ("either fix the root cause
  or... document that conclusively") treats a real fix as the preferred outcome when the cause turns out
  tractable, which it did here (two missing headers, not an environment limitation).

## Consequences

- `services/gateway-service/tests/test_api.py`: `_register_instance`/`_deregister_instance` send
  `X-DMS-Principal`; `_drain_instance` sends the operator bearer token; new `REGISTRY_OPERATOR_KEY`
  module constant.
- `infra/docker-compose.yml`: `registry-service` gains `DMS_REGISTRY_OPERATOR_KEY`, a genuinely new
  dev-stack capability — drain/activate is now actually reachable in this environment, not just in a
  future real installation that configures its own key. A useful side effect beyond this session's own
  test-fixing goal: any future session that needs to exercise drain live (rolling-update-style manual
  testing) now can, without first discovering this same gate blind.
- `gateway-service`: 31/31 passed (was 28/31), confirmed stable across two consecutive runs. `ruff`
  clean on this service's own files.
- **This is the last queued session of Phase 73.** A full unfiltered `scripts/run-tests.sh` regression and
  `graphify update .` are due at its close, per the standing phase-end rule — expect the "gateway-service's
  same 3 pre-existing failures" line to finally disappear from that run's summary, and from every future
  one.
