# 0200 — Correction: revert P66-S2's business_key creation-time validation, reorder case-service

**Status:** accepted
**Context:** P68-S1's own Definition of Done requires a full, unfiltered backend regression
(`scripts/run-tests.sh`, every service) before the per-service Postgres role rollout could be considered
verified. This was, as far as can be determined, the first time that specific command (no service filter)
had been run since P66-S2 shipped — every session between P66-S2 and this one only ever ran a
service-scoped subset (`scripts/run-tests.sh <touched-services>`), so a real, previously-undetected
regression in P66-S2 went uncaught until now.

## Decision

**Revert P66-S2's `POST /instances` creation-time `business_key` validation entirely.** P66-S2 added a
check, at instance-creation time, that a non-`None` `business_key` must resolve against case-service or
document-service via `_resolve_business_key_scope`, rejecting an unresolvable value with `422`. This
broke `case-service`'s own, real, load-bearing case-creation flow unconditionally: `case_service.main.
create_case` generates `case_id`, then calls `workflow-service`'s `POST /instances` with
`business_key=case_id` **before** the `Case` row is created (the row is created only afterward, so its
initial status can reflect a synchronously-completed instance — see `case_service.main.create_case`'s own
comment on this ordering, added Post-Roadmap Phase 44 Session 4 for an unrelated event-race reason). Since
P66-S2, that business_key could therefore never resolve — case-service's `GET /cases/{id}` always
returned `404` at the moment workflow-service checked it — so **every single case creation, for any
process definition, unconditionally failed with `422`** from the moment P66-S2 shipped. `mail-connector`
(case-matching) and the affected halves of `case-service`'s own test suite failed identically, for the
same root cause.

**Also fixed, kept (not reverted): `case-service` now creates and commits its `Case` row before calling
`workflow-service`.** `process_instance_id` was already nullable for exactly this reason and is filled in
via an `UPDATE` once the instance actually exists. This is independently more correct regardless of the
validation revert — a business_key should reference something real before it's used as a reference — and
protects against a *future* reintroduction of similar validation. On a genuinely unknown
`process_definition_id`, the just-created row is removed again via a new, narrowly-scoped
`repository.delete_unstarted_case` (a rollback-only helper, not a general "delete a case" capability,
which this codebase deliberately has none of) before returning `400`, preserving the original "nothing
persisted on a 400" contract.

**Also fixed, kept: `workflow-service`'s `CaseServiceClient.get_case`/`DocumentServiceClient.get_document`
now catch `httpx.TransportError`** (a genuine connection failure — DNS, refused connection, timeout — as
opposed to an ordinary `403`/`404` HTTP response, which these methods already treated as "does not
resolve here"). Previously a transport-level failure propagated as an unhandled exception, crashing the
caller with `500` instead of degrading gracefully. Both are independently valuable robustness fixes,
unrelated to whether the creation-time validation exists — `_resolve_business_key_scope` is still used for
its original, P32-S2 purpose (delegation-scope resolution at task-completion time), where a transport
failure could still occur.

## Rationale

- **Why revert the validation rather than fix the ordering conflict on workflow-service's side**: even
  after fixing case-service's production ordering (case row committed before the call), a second,
  independent, unavoidable problem remained: case-service's own test suite runs in-process (`TestClient`,
  no real network listener), so the live, deployed `workflow-service` container used by those same tests
  can never reach a real, network-visible case-service to resolve against during a test run — a genuine
  structural conflict between "validate against a live peer" and "this peer's own tests run in-process
  by convention" (case-service is one of this project's `CONSUMER_SERVICES`, stopped before its own test
  run for an unrelated NATS-durable-consumer reason, per `scripts/run-tests.sh`). Solving this without
  reverting would require either a materially larger test-infrastructure change (switching case-service's
  own test suite from in-process `TestClient` to real-HTTP-against-a-live-container, matching
  `config-service`/`migration-service`'s own established pattern) or a bypass mechanism controllable only
  by a trusted service identity (itself a new, non-trivial authorization surface). Both are disproportionate
  to the validation's own value.
- **Why the validation's own value didn't justify either alternative**: it caught an accidental
  typo/garbage `business_key` early — a data-quality nicety, not a security or correctness requirement.
  `business_key` remains, as it always has (per `models.py`'s own long-standing docstring), a genuinely
  opaque cross-service reference with no FK enforcement — consistent with how `folder_id`/`object_type_id`
  are treated elsewhere in this codebase.
- **Why keep the case-service reorder despite reverting the thing that motivated it**: it's independently
  correct sequencing (create the referenced row before referencing it) with no downside — `process_instance_id`
  was already nullable, and the reorder even *removes* a narrow window where a case existed only in
  workflow-service's response but not yet in case-service's own database.
- **Why keep the transport-error handling despite reverting the validation that surfaced it**: an
  unhandled `500` on a transient network blip during a delegation-scope lookup at task-completion time
  (the validation's original, P32-S2 purpose, unaffected by this revert) is a real robustness gap on its
  own merits, independent of whether creation-time validation exists.
- **Why this warrants a correction ADR rather than silently reverting**: this project's established
  discipline (used throughout the Phase 65+ round) is to document every correction of a prior session's
  work transparently, including why the original decision (ADR 0195) turned out to be wrong and what was
  learned — the miss here (a genuinely load-bearing cross-service integration path never exercised by any
  session's own regression testing between P66-S2 and now) is itself worth recording as a reason to run
  the full, unfiltered suite periodically, not just service-scoped subsets.

## Consequences

- `services/workflow-service/src/workflow_service/main.py`: `start_instance`'s validation block removed.
- `services/workflow-service/src/workflow_service/models.py`: `ProcessInstance.business_key` docstring
  corrected.
- `services/workflow-service/src/workflow_service/case_client.py`,
  `services/workflow-service/src/workflow_service/document_client.py`: both now catch
  `httpx.TransportError` and degrade to "unresolved" instead of crashing.
- `services/workflow-service/tests/test_api.py`: removed the now-obsolete
  `test_start_instance_with_unresolvable_business_key_is_422`; `test_start_instance_with_manual_task_stays_running`
  reverted to a plain string `business_key` (no longer needs a real document).
- `services/case-service/src/case_service/main.py`: `create_case` reordered — case row created and
  committed first, `workflow_client.start_instance` called after, `process_instance_id` filled in via a
  follow-up update; new `repository.delete_unstarted_case` rollback helper for the "unknown process
  definition" failure path.
- [ADR 0195](0195-workflow-service-business-key-validation-and-reassign-authorization.md)'s creation-time
  validation half is superseded by this ADR; its `reassign_task` supervisor-authorization half is
  unaffected and remains in place.
- Tests: `workflow-service` 227/228 passed (one pre-existing, unrelated flake in the federation/xdomea-
  dispatch test family, already documented since P66-S2's own session); `case-service` 87/87 passed (was
  25 failed/50 passed/12 errors); `mail-connector` 80/80 passed (was 77 passed/3 errors) — both fully
  fixed by this correction, confirming the shared root cause. `workflow-service` rebuilt/redeployed.
- **Two additional test failures found during this session's full regression, confirmed pre-existing and
  unrelated to this correction or to P68-S1's own Postgres-role work**: `auth-service` (6 failed/8 errors)
  — root-caused via live reproduction to a circular dependency from ADR 0190/P63-S1's superuser-bypass
  check: `permission-service`'s `_require_role_management` always calls back to `auth-service` to check
  active-superuser status, but `auth-service`'s own container is stopped during its own test run (a
  `CONSUMER_SERVICES` entry, per `scripts/run-tests.sh`, for an unrelated NATS-durable-consumer reason) —
  the same structural conflict class this ADR's own case-service fix addresses, just pre-existing and
  between different services, never caught before this session's first full regression run either.
  `webdav-connector` (6 failed) — confirmed to match ADR 0189's already-documented, pre-existing root-
  `PROPFIND`-timeout pattern exactly (`ReadTimeout` against this shared dev stack's accumulated `root`
  folder). Neither fixed here — both out of scope for this session, flagged for a future one.
  `gateway-service` (3 failed, `401` from a real `registry-service` call) was found but not fully
  root-caused; also flagged, not fixed.
