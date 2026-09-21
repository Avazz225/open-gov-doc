# 0181 — registry-service: gate every mutating instance endpoint

**Status:** accepted
**Context:** P59-S4 (Phase 59, "Critical Authorization Bugs" — fourth session of the sixth gap-analysis
round's live-code security sweep). `registry-service` had **zero authorization** on any mutating instance
endpoint (`POST /instances`, `POST /instances/{id}/heartbeat`, `POST /instances/{id}/drain`,
`POST /instances/{id}/activate`, `DELETE /instances/{id}`) — confirmed the entire file had no
`Depends()`-based auth beyond what the gateway itself provides (a valid JWT). Since
`gateway_service.upstream.InstanceResolver.resolve()`/`pick()` (`random.choice()` over the registered
pool) has no allowlist of legitimate `service_type` values or `address` origins, any authenticated user of
any role could `POST /instances {"service_type": "document-service", "address":
"http://attacker.example.com", ...}` through the gateway and have a real chance of being selected for real
user traffic on subsequent requests — including uploaded document bodies and the gateway-injected
`X-DMS-Principal`/`X-DMS-Username`/`X-DMS-Roles` headers, a traffic-hijack/credential-harvesting vector,
not just a routing curiosity. The same unauthenticated surface also let any user `POST
/instances/{id}/drain` or `DELETE /instances/{id}` on REAL instances, taking them out of rotation or
deregistering them entirely — a fleet-wide denial-of-service.

## Decision

Two different gate shapes for two different caller classes, chosen after tracing every real caller first
(same discipline as P59-S1/S2/S3):

- **`POST /instances`, `POST /instances/{id}/heartbeat`, `DELETE /instances/{id}` — self-service gate.**
  Every one of this project's ~31 self-registering services calls these exclusively via the shared
  `dms_registry_client.RegistryRegistration`/`maybe_start_registration`, always on its own behalf. The
  fix: the caller's own `X-DMS-Principal` must equal the `service_type` it is acting on — for
  `POST /instances`, the `service_type` in the payload; for heartbeat/deregister, the target instance's
  own `service_type` (existence-checked first, same ordering convention as the rest of this project).
  `RegistryRegistration`'s internal `httpx.AsyncClient` now sends `X-DMS-Principal: <its own
  service_type>` as a default header — **one change in the shared library covers every self-registering
  service automatically**, rather than touching 31 individual call sites (a materially simpler rollout
  than P59-S2's six separate client files, since here a single shared client class already exists).
  Unspoofable by a real end user through the gateway for the same reason every other fixed-identity gate
  in this project is: the gateway's `proxy()` handler always overwrites any client-supplied
  `X-DMS-Principal` with the verified JWT claims, so a real user's principal is their own Keycloak `sub`,
  never a literal service-type string.
- **`POST /instances/{id}/drain`, `POST /instances/{id}/activate` — operator-key gate.** Traced the real
  caller: `scripts/rolling-update.sh` (an external, human-operator-run ops script, confirmed via grep —
  `admin-ui` has no drain/activate call anywhere), not a self-registering service and not any backend
  service acting on its own behalf. Gated instead via `_require_operator_key`, the exact same
  `Authorization: Bearer <key>` shape as `federation-hub-service`'s existing `hub_operator_key`
  (`services/federation-hub-service/src/federation_hub_service/main.py`'s `revoke_installation`) — fully
  locked (`403`) unless the registry operator deliberately sets `DMS_REGISTRY_OPERATOR_KEY`. Not an
  `X-DMS-Principal` check: there is no single "owning service" for a drain/rollback decision, it is
  inherently an external operational action.

## Rationale

- **Why two different gate shapes in one session, not one uniform mechanism**: reusing the self-service
  `X-DMS-Principal` gate for drain/activate would have required inventing a caller identity for
  `rolling-update.sh` that doesn't naturally exist (it isn't "a service" in this project's sense), and
  reusing the operator-key gate for register/heartbeat/deregister would have meant distributing one shared
  secret to all 31 services just to prove "I am myself" — a real secret-management burden for a claim a
  header comparison already settles for free. Matching the gate shape to the real caller, confirmed by
  tracing before choosing, keeps both minimal.
- **Why the operator key is `None`-by-default (fully locked, not permissive-by-default)**: exactly
  mirrors `hub_operator_key`'s own accepted precedent — an operator must deliberately opt in to enable a
  destructive/disruptive capability, rather than the capability being silently open until someone thinks
  to lock it down.
- **Why a missing `X-DMS-Principal` on heartbeat/deregister collapses into `403`, not a separate `401`**:
  existence (`404`) is checked first for both endpoints, matching this project's established
  existence-before-permission convention (verified against `document-service`'s own pattern during
  P59-S3). Once existence is confirmed, an empty header can never equal a real instance's `service_type`
  anyway, so a missing header and a wrong one produce the identical, single `403` outcome rather than a
  separate pre-existence-check branch that would have to run before the `404` to still emit `401`.
  `POST /instances` has no existing row to protect an oracle for, so it keeps the more precise
  `401`-then-`403` split.
- **Why the internal cleanup loop (`_cleanup_poll_loop`, Phase 58 Session 2) is exempt**: it already reads
  each stale instance's own `service_type` before deregistering it, and passes that value through as its
  own identity — the new check exists to stop an *external* caller deregistering an instance it doesn't
  own, not to gate this already-instance-scoped internal loop.

## Consequences

- New endpoints/behavior: `POST /instances` now `401`s with no `X-DMS-Principal`, `403`s when it doesn't
  match `service_type`. `POST /instances/{id}/heartbeat` and `DELETE /instances/{id}` now `404` for an
  unknown instance, else `403` unless `X-DMS-Principal` matches that instance's own `service_type`.
  `POST /instances/{id}/drain` and `.../activate` now `403` without a valid
  `Authorization: Bearer <registry_operator_key>`.
- `libs/dms-registry-client/src/dms_registry_client/client.py`: `RegistryRegistration.__init__` now
  constructs its default `httpx.AsyncClient` with `headers={"X-DMS-Principal": service_type}` — every one
  of the ~31 services importing this shared library picks up the fix automatically on rebuild, no
  per-service source change needed.
- `scripts/rolling-update.sh`: reads `REGISTRY_OPERATOR_KEY` from the operator's own shell environment,
  sends it as `Authorization: Bearer <key>` on both `/drain` calls; `docs/operations/rolling-updates.md`'s
  manual rollback instructions updated with the same header requirement.
- New tests: `registry-service` +9 (`test_register_without_principal_header_is_401`,
  `test_register_with_mismatched_principal_is_403`, `test_heartbeat_with_wrong_principal_is_403`,
  `test_deregister_with_wrong_principal_is_403`, `test_drain_without_operator_key_is_403`,
  `test_activate_without_operator_key_is_403`, plus `test_repository.py`'s
  `test_heartbeat_with_wrong_principal_raises`/`test_deregister_with_wrong_principal_raises`), 54/54 total
  (up from 45 pre-session, +9 net after also updating ~15 existing call sites with the now-required
  headers). All 32 affected services (registry-service + the 31 importing `dms_registry_client`) rebuilt
  and redeployed together in one batch — a rolling, staggered redeploy would have left already-running
  old-image containers unable to heartbeat successfully against the new gate (a `403`, not a `404`, so the
  client's own 404-triggered self-healing re-registration would never fire), a real availability risk this
  session deliberately avoided by not rebuilding registry-service alone first.
