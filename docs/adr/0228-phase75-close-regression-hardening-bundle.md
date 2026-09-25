# 0228: Phase 75 Close — Regression-Hardening Bundle (Seven Unrelated Pre-Existing Bugs)

## Status

Accepted (Phase 75 close session).

## Context

Phase 75's own work (Tailwind CSS v4 migration, P75-S1 through S10, ADRs 0218-0227) touched only
frontend files — zero backend code changed. The phase-close Definition of Done still requires a full,
green `scripts/run-tests.sh` pass before the phase can close. That pass surfaced a cascade of pre-existing
backend failures, none caused by Phase 75, that took a full session to properly diagnose because they
compounded: an initial hang (a real bug) blocked the first run entirely; a full `docker compose down -v`
taken to rule out test-state pollution then exposed several more real, previously-masked bugs that only
manifest on a genuinely fresh installation (this dev stack's Postgres volume had survived, warm, across
75+ prior phases without ever being fully wiped); and two of the remaining failures turned out to be
flaky pytest fixture-ordering bugs, not backend bugs at all. Each is a small, independent, well-scoped
fix — bundled into one ADR since none individually rises to "new architectural decision," but the
collection as a whole is worth a permanent record so a future session doesn't have to re-derive any of
this archaeology.

## Decisions

**1. `signature-service`/`migration-service`: background-task-cancellation DB-connection leak.**
`lifespan()`'s shutdown called `<task>.cancel()` without awaiting the now-cancelled task before disposing
the engine — every OTHER service in this project already pairs `.cancel()` with
`with suppress(asyncio.CancelledError): await <task>` (confirmed by auditing all ~24 services with a
background poll task); these two were the only stragglers. A cancelled-but-not-awaited task's DB session
can leak an open, "idle in transaction" Postgres backend connection that outlives the task, which — on a
fresh install — can hold a lock that blocks a *later* test's own `ALTER TABLE` migration indefinitely (a
1h39m real hang is what surfaced this). Fixed by adding the same `cancel()` + `await` pairing used
everywhere else. Files: `services/signature-service/src/signature_service/main.py`,
`services/migration-service/src/migration_service/main.py`.

**2. `auth-service`/`workflow-service`: crash on a Federation Hub registration failure during first-time
setup.** `_ensure_federation_identity()`'s `if identity is None:` (first-ever registration) branch let any
exception from the Hub round-trip propagate uncaught, aborting `lifespan()` entirely — the sibling
`else:` branch (re-registration on every restart) was already correctly wrapped in
`try/except: logger.warning(...)`, with an explicit docstring stating registration is "opt-in... not a
hard dependency of this installation." The first-time path just never got the same treatment. On a
long-lived dev DB this was invisible (an old `FederationIdentity` row created before this repo's SSRF
guard existed, see Decision 7, meant the `else` branch was the only one ever exercised); a genuinely fresh
install hits the `if` branch and previously crashed the whole service on startup. Fixed by wrapping the
first-time branch in the same try/except, leaving `identity` unset (and thus unregistered, retried on the
next restart) on failure. Files: `services/auth-service/src/auth_service/main.py`,
`services/workflow-service/src/workflow_service/main.py`.

**3. `auth-service`: no retry on the domain-admin role-assignment bootstrap.** A single best-effort
attempt to grant each domain-admin `TechnicalAccount` its `permission-service` role, with no retry and a
docstring literally titled "no retry loop, self-heals on the next restart." `docker-compose.yml`'s
`depends_on: permission-service` uses `condition: service_started`, not `service_healthy` (unlike
`keycloak`) — a real, easily-hit race on a multi-container cold boot where `auth-service`'s process starts
before `permission-service`'s HTTP server is actually accepting requests. Domain-admin accounts are core
functionality (4.6), not an opt-in bonus, so a bounded retry (5 attempts, 1s apart) is the right middle
ground between "crash" and "silently broken until an operator happens to restart this service." File:
`services/auth-service/src/auth_service/main.py`.

**4. `permission-service`: unhandled unique-constraint race on `POST /role-assignments`.** Every
"list existing, create if missing" idempotent-grant fixture across this entire test suite (and the
equivalent admin-ui flow) hits this endpoint the same way, with no coordination between callers — two
concurrent identical grant attempts raced on `uq_assignment`'s unique index, and the loser got an
unhandled `IntegrityError` surfaced as a bare 500. This service's own `get_effective_permissions()` cache
population already hit and fixed the identical class of race in P31-S4/ADR 0115, via
`INSERT ... ON CONFLICT DO NOTHING` + re-read; `create_role_assignment()` gets the same treatment now.
File: `services/permission-service/src/permission_service/repository.py`.

**5. Seven services: `_is_active_superuser()`'s call to `auth-service` had no error handling.** The exact
same helper function — a break-glass-superuser bypass check, copied verbatim across `license-service`,
`monitoring-service`, `permission-service`, `plugin-orchestration-service`, `query-service`,
`reporting-service`, `workflow-service` — called `auth_client.get_active_superuser()` with no try/except.
Since this check gates nearly every permission-changing endpoint in `permission-service` specifically
(`POST`/`PUT /roles`, `POST /role-assignments`, `PUT /approval-config/*`, all via
`_require_role_management`), any transient `auth-service` unavailability (including `auth-service`'s own
container being stopped/restarted mid-suite by `scripts/run-tests.sh`'s own NATS-durable-consumer
handling) turned into an unhandled 500 for every OTHER service's test setup that grants itself a role —
this was the root cause tying together `auth-service`'s own remaining test failures with
`notification-service`'s and `reporting-service`'s cascading 500s. The check's own purpose (an *optional*
bypass) means failing safe — treating an unreachable `auth-service` as "no active superuser" — is strictly
the correct security posture too (fails closed on the *privilege*, not open), not just a crash fix. Fixed
identically in all seven files.

**6/7. `notification-service`/`reporting-service`: flaky pytest fixture-ordering bug (not a backend bug).**
Both services define two session-scoped `autouse=True` fixtures with an *implicit* dependency — one grants
`ROLE_ADMIN_PRINCIPAL_ID` the `domain-admin-users` role (`_grant_role_admin_permission`), the other
(`_grant_notification_write_permission` / `_grant_document_read_permission`) uses that principal's
authority via an `X-DMS-Principal` header — but neither declares the dependency as a fixture parameter.
Same-scope autouse fixtures with no declared relationship resolve in an *unspecified* order in pytest;
`--setup-show` confirmed live that this genuinely varies run to run (observed both orders across today's
many runs, including a case where `reporting-service` passed standalone twice and then failed inside the
full suite with the identical code unchanged). When the grant fixture happened to resolve second, the
consumer fixture's `POST /roles` call correctly got 403 (the principal genuinely had no permission yet).
Fixed by declaring the explicit dependency (`async def _grant_notification_write_permission(_grant_role_admin_permission):`), which pytest resolves deterministically. Files:
`services/notification-service/tests/test_api.py`, `services/reporting-service/tests/test_api.py`.

**Note on scope (audited in a follow-up pass, corrected from this ADR's original draft)**: an initial
`grep -rln "ROLE_ADMIN_PRINCIPAL_ID" services/*/tests/*.py` suggested roughly a dozen more services might
share this exact risk. A precise, per-file audit of every match found the actual vulnerable shape — a
session-scoped `autouse=True` fixture calling a *gated* endpoint (`POST /roles`, the only one that
requires `X-DMS-Principal`) using a principal granted by *another* session-scoped `autouse=True` fixture,
with no declared dependency between them — exists **only** in the two services already fixed above.
Every other `ROLE_ADMIN_PRINCIPAL_ID` usage found falls into one of three provably safe shapes instead:
(a) a function-scoped, on-demand fixture like `everyone_role_without` (`archival-service`, `audit-service`,
`auth-service`, `case-service`, `reporting-service`, `virus-scan-service`) — safe because pytest resolves
all session-scoped fixtures before any function-scoped one, regardless of same-scope ordering; (b) direct
use inside a test function body (`rendering-service/tests/test_api.py`) — safe for the same
scope-ordering reason; (c) other session-scoped `_grant_*_permission` fixtures
(`archival-service`/`audit-service`/`case-service`/`document-service`/`folder-service`/
`rendering-service`/`search-service`/`virus-scan-service`) that only call the *ungated*
`POST /role-assignments` endpoint directly, needing no admin principal at all. No further action needed
— this was a complete fix, not a partial one.

## Findings documented, not fixed (both require the user's own product/security judgment)

**Federation self-registration is permanently blocked by the Phase 60 SSRF guard in this bundled local
dev environment.** `federation-hub-service`'s `_validate_callback_base_url` (Phase 60 Session 1) rejects
any `callback_base_url` that resolves to a private/loopback/link-local/reserved address — correct,
intentional behavior for real cross-installation federation, where a peer's callback address must be a
genuine external address. In this project's own bundled `docker-compose.yml`, `auth-service`/
`workflow-service` register with their own Docker-internal address (`http://gateway-service:8000/...`),
which *always* resolves to a private IP on the Docker network — the guard therefore rejects it *every
time*, unconditionally, for any environment that doesn't already have a `FederationIdentity` row that
predates the guard's introduction. This was invisible for months because this dev stack's Postgres volume
has persisted continuously since before Phase 60, so the (now-safely-crash-proofed, per Decision 2)
re-registration path silently warns-and-continues on every restart, reusing the old grandfather-claused
identity. Today's `docker compose down -v` wipes (done to rule out test-state pollution while diagnosing
Decisions 1-6) destroyed that old data and exposed the underlying state honestly: **~10 federation tests
in `auth-service`/`workflow-service` (`test_directory.py`'s federated-search tests,
`test_federation.py`, `test_xdomea_handoff.py`) cannot pass in a genuinely fresh instance of this bundled
local dev environment**, and never could have, once the guard shipped. Real cross-installation federation
between actually-separate installations (the guard's real target) is unaffected, since real peers use real
external addresses.

Asked the user how to proceed (relax the guard for Docker-internal addresses in local dev; disable
`DMS_FEDERATION_HUB_BASE_URL` for local dev entirely and mark the dependent tests `xfail`; or document
only). **User chose: document only, no code change.** These ~10 tests remain a known, understood,
non-blocking condition for this specific bundled local-dev topology — not a regression, not blocking any
future phase's regression gate, and not evidence of a real security problem (the guard is doing exactly
what it was built to do).

**`webdav-connector`'s root `PROPFIND` O(N) degradation (already named as an open item, Phase 50 bundle)
reproduced live again this session** — timing out under this dev stack's accumulated test-data volume
after many additional test runs since Phase 74 close first flagged it. Unrelated to anything touched this
session; already tracked; not re-fixed here (same reasoning as Phase 74 close: needs real pagination
work).

## Consequences

- All seven numbered fixes are narrow, mechanical, and low-risk (added error handling / explicit
  ordering / a documented-elsewhere upsert pattern) — none change any endpoint's documented behavior for
  a caller that was already succeeding.
- The federation-registration finding means: any FUTURE fresh `docker compose down -v` in local dev will
  continue to show these same ~10 test failures, predictably, forever, until one of the three options
  above is chosen. This is now the expected, documented baseline for a fresh install — not something a
  future session should spend time re-diagnosing from scratch.
- The fixture-ordering bug class (6/7) is confirmed real and non-deterministic, but a full follow-up
  audit (same session) found its actual footprint was exactly these two services, not the dozen the
  initial `grep` suggested — no further fixture-ordering work is needed.
