# 0257 — Rust for selected services: scoping

**Status:** accepted. User decision 2026-11-25: the Python levers (section 3) come first, as P95-S2a
(queue groups, Helm rollouts), P95-S2b (permission-service hot path without the ORM, several processes)
and P95-S2c (measurement). The Rust pilot (P95-S3) follows only if the criterion of section 7 is met
against that state.
**Context:** P95-S2, no code. Input: the three measurements in `docs/operations/performance-ranking.md`.
- **P95-S1:** base and extended run, rankings, py-spy.
- **P95-S1e:** after the fixes S1a–d.
- **P95-S1f:** two extended runs back to back, after the fixes from S1e's findings.

## 1. Which services qualify

The rule from the plan: only services where their own compute or memory dominates. Where time goes to
waiting, queries, caching or pools, tuning is the cheaper fix. P95-S1 to S1f showed that every collapse
had such a cause: scans, a shared pool, a cache stampede, global invalidation, an unpaged list, a pool too
small, a LibreOffice profile built per call. All were fixed in Python.

| Service | Measured | Verdict |
|---|---|---|
| **permission-service** | On every request path, 1.1–1.4 calls per user request. Base run: 3.7 ms CPU per request, 22.6 % latency share. Full extended load (P95-S1f): own time 53–64 ms per call, latency share 28–33 %, pool at its 30. The bottleneck once everything else was fixed. | **Candidate** |
| **gateway-service** | Every request. Base: 3.3 ms CPU per request, 9.9 % latency share. Extended: 12.9–24.3 ms own time per call, 7–9 %. In the profile about a third is the httpx client, a tenth Starlette. | Candidate, second |
| workflow-service | After the index and the pool: DB 27–29 % of its work, BPMN engine (SpiffWorkflow) in Python | No: the engine is a library with no Rust counterpart; the costs were algorithmic |
| rendering-service | Largest CPU consumer, but in `soffice` and Pillow (native). Profile reuse: 3–10 → 680–814 renditions/min | No |
| document-, folder-, storage-service | Time in drivers and in calls to other services | No |
| case-, mail-connector, the rest | Light, or fixed algorithmically | No |

**RAM is not a criterion.** Every Python service sits at 85–195 MB and barely grows under load.

## 2. Where permission-service's CPU actually goes

py-spy, innermost frame, both runs (base 9,309 samples, extended 8,996):

| Layer | Share |
|---|---|
| SQLAlchemy (ORM 12–13 %, Core/engine 21–23 %, other 7 %) | **about 41 %** |
| asyncio event loop | 17 % |
| HTTP framework (Starlette/FastAPI/uvicorn) | 11–13 % |
| asyncpg, the actual driver | 4.5 % |
| own middleware (sensors, access log, tracing) | 3 % |
| service code | 2 % |
| other (stdlib, libraries) | 18–19 % |

**Reading:** a check runs a handful of small queries (cache generations, the cached row, on a miss the
ancestor walk). Most of the cost is the Python layer around each query, not the database and not the
permission logic.

A Rust service would remove that layer. **So would a hot path without the ORM** in Python:
- `GET /check`, `POST /check/batch` and the effective-permissions read;
- SQLAlchemy Core with precompiled statements, or asyncpg directly.

## 3. Two Python levers come first

**(a) More processes: blocked today, verified.**
- permission-service holds no request state in memory (the cache is in Postgres), so several processes
  could share the load.
- But every service with a durable NATS consumer subscribes without a queue group. A second instance
  dies at startup. Started live in P95-S2, a second permission-service container exited with
  `nats: JetStream.Error consumer is already bound to a subscription`.
- `docs/operations/rolling-updates.md` records this for the compose rolling-update script (P10). It
  also applies to the Helm chart, which says nothing about it:
  - Deployments use Kubernetes' default `RollingUpdate`, so `helm upgrade` of any consumer service starts
    a surge pod that crash-loops until the old pod is gone. The rollout hangs.
  - Enabling `autoscaling` (off by default, `maxReplicas` 3–5) would hit the same wall.
- **Fix:** queue groups in `dms-eventbus-client` (`deliver_group`, one per durable), with a migration for
  the existing durables (they can't gain a deliver group in place).
- This is needed with or without Rust: for Helm rollouts, for the HPA, and for any second replica.

**(b) A hot path without the ORM**, see section 2.

Both are Python changes without a second language. **The pilot's criterion must be measured against
Python after (a) and (b), not against today's.**

## 4. What a Rust service needs

The shared Python libraries it would have to match:

| Library | Lines | What it does | For permission | For gateway |
|---|---|---|---|---|
| dms-common | 718 | settings (`DMS_*` env), JSON logging with trace ids, the `dms.access` log, acting identity (`X-DMS-Principal`/`On-Behalf-Of`, service principals), OpenTelemetry setup (OTLP, sampler with remote ratio), bounded shutdown | yes | yes |
| dms-db-base | 108 | engine and pool budget from `DMS_DB_POOL_*`, schema-per-service base | yes (sqlx pool) | no |
| dms-eventbus-client | 304 | NATS JetStream publish and durable subscribe, envelope, trace headers, concurrency, `consumer_pending` | yes (2 consumers, publisher) | no |
| dms-metrics-client | 997 | sensor catalogue with group and cost, guarded metrics, on-scrape samplers, polling of the sensor config, runtime and pool sensors, HTTP sensors | yes | yes |
| dms-registry-client | 143 | self-registration with sensor declarations, heartbeat | yes | yes |
| dms-auth-client | 141 | JWT validation against JWKS | no | yes |
| dms-permission-client | 188 | `/check`, `/check/batch`, maintenance state | no | yes (maintenance) |

**Decision: shared crates** in a Cargo workspace (`rust/crates/dms-common`, `dms-db`, `dms-eventbus`,
`dms-metrics`, `dms-registry`, later `dms-auth`), not per service.
- Both candidates need four of them.
- A copy per service would drift from the Python originals, the way the libraries once drifted between
  services before they existed.
- **The crates follow the Python libraries,** which stay the reference. Every convention (header names,
  envelope fields, sensor names, log fields) is mirrored and checked by the conformance suite, not
  re-decided.

**The database schema is a prerequisite.**
- permission-service creates its schema at startup today: `create_all` plus eight ad-hoc `ALTER`/`CREATE
  INDEX` statements.
- A Rust service can't run SQLAlchemy's `create_all`.
- The schema must exist as versioned SQL migrations first. That is ADR 0260's option B, formalising
  migrations in `dms-db-base`, scoped in P94-S4 and not yet built. Both implementations then apply the
  same migration files.

## 5. Contract and conformance

- **Same contract:** identical HTTP API (FastAPI's generated `openapi.json` is the reference and is
  diffed against the Rust service's), identical events (subjects, envelope, payloads), identical schema
  (the shared migrations).
- **permission-service conformance:**
  - 101 of its 116 API tests (`test_api.py`) are pure HTTP.
  - A fixture switch makes them a conformance suite: `DMS_CONFORMANCE_BASE_URL` set means an
    `httpx.Client` against that URL instead of `TestClient(app)`.
  - The tests already reset the test database at the SQL level, so they work against any implementation
    that uses it.
- **What doesn't transfer:**
  - 15 API tests that monkeypatch or reach into `app.state`;
  - the repository-level tests (77 + 8 + 13 consumer tests, about 100), which call Python functions;
  - among them the race-condition tests of ADR 0239 and 0263 (a recompute paused while a grant commits).
  
  Those guarantees must be expressed again, as Rust unit tests with the same interleavings or as
  concurrency tests over HTTP. **The plan's "runs unchanged" holds for about half of the suite.**
- **gateway-service conformance:** even its HTTP-level tests inject the JWKS, validator and rate limiter
  through `app.state`. A conformance run needs these configurable from outside (test JWKS by URL, Redis
  URL), which takes groundwork first.

## 6. Rollout and rollback

- **Same identity:** the same registry service type and gateway route. The Helm value `image` per
  service selects Python or Rust. Rollback is switching the image back.
- **No traffic split** between the two implementations at first. A canary needs two instances side by
  side, which the consumer issue (section 3a) blocks today.
- **Shared database and migrations:** a rollback after a migration needs the migration to be compatible
  with the previous version (the expand/contract rule ADR 0260 recorded).

## 7. Criteria for continuing after the pilot

- **CPU per 1,000 requests** in the base and extended k6 runs, measured like P95-S1/S1f: **at least 2×
  better than the Python service after levers 3a and 3b**, at the same or better p95. Less than that
  doesn't justify a second language.
- **p95 under the full extended load** as the user-facing check.
- **RAM** only as a guard (no regression), not a goal.
- **The costs, written down before the decision:**
  - a second toolchain in the Docker builds, CI and SBOM (`scripts/sbom.sh`);
  - keeping the crates in step with the Python libraries;
  - on-call knowledge and hiring.

## 8. Effort, roughly

- **permission-service:** 4,866 lines, 52 endpoints, two event consumers, a publisher, the four-eyes
  approval flow, scope locks, delegations, groups, the generation logic of ADR 0239/0263. Several
  sessions, plus the crates and the migration groundwork.
- **gateway-service:** 879 lines, two routes (proxy and health), but JWT, rate limiter, bulkhead,
  registry resolution, maintenance mode. Faster to build, smaller payoff (3.3 ms CPU per request).

## 9. Recommendation

**Don't start the Rust pilot next.** First:
1. **Queue groups in the event-bus client and a Helm rollout fit for consumer services.** This fixes the
   rollout defect from section 3a and makes a second replica possible.
2. **A hot path without the ORM in permission-service**, then permission-service with 2–4 processes or
   replicas.
3. **Measure again** with the same profile.

Then decide on the pilot (P95-S3) with that as the baseline:
- **If permission-service still limits the full load and Rust promises at least 2×:** pilot with
  permission-service, after the schema migrations (ADR 0260 B) and the conformance switch.
- **If not:** no pilot. The criteria of section 7 stay as the yardstick for later.
