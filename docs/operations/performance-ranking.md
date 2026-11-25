# Per-service performance ranking (P95-S1, 2026-11-24)

The input for P95-S2, the scoping of a Rust pilot (ADR 0257). It asks where the services spend time and
resources under load, and which of that a faster implementation could fix and which needs a
different algorithm.

> **Second measurement (P95-S1e, 2026-11-25):** after the fixes P95-S1a–d, see
> [the section at the end](#second-measurement-p95-s1e-2026-11-25). The sections up to "What follows"
> are the first measurement, unchanged except for one correction. **Third measurement (P95-S1f):** two
> extended runs back to back, [further down](#third-measurement-p95-s1f-2026-11-25). **Fourth
> measurement (P95-S2c):** the Python baseline for the Rust decision,
> [at the very end](#fourth-measurement-p95-s2c-2026-11-25-the-python-baseline-for-adr-0257).

## How it was measured

- **Profile:** the agreed k6 profile (10 min ramp to 200 VUs, 5 min hold, 5 min ramp down), run twice:
  - **base:** list, download, search, upload;
  - **extended** (`SCENARIO=extended`, new): adds start, find and complete a circulation, create and
    list cases, renditions, OCR results, mailboxes and the mail inbox, with a share of the base mix.
- **Stack:** the dev stack on one host (Ryzen 9 7900X, 64 GB). Every service is one uvicorn worker.
- **Tracing** at 10 % (ADR 0250). Its cost is under 0.5 ms per request (P94-S1).
- **Sources:**
  - cadvisor (CPU, RSS per container);
  - the services' own `http_requests_total` and duration histograms;
  - Tempo (500 sampled traces per run, cut at CONSUMER spans: own time, DB time and waiting per service);
  - py-spy profiles under load (60 s for folder-service in the base hold phase; 45 s for permission-service
    and gateway in the extended hold phase);
  - a separate cold-cache measurement.
- **Tools** (in `loadtest/`): `rank_services.py` (both rankings from a time window),
  `profile_layers.py` (py-spy samples by layer), `cold_cache.py`.

## Base run: healthy

73,770 requests, 61/s, no errors, goodput 99.93 % (SLA 1 s).

| Action | p50 | p95 | p99 |
|---|---|---|---|
| search | 8.6 ms | 30 ms | 66 ms |
| list_folder | 19.9 ms | 103 ms | 292 ms |
| download | 34.2 ms | 180 ms | 359 ms |
| login | 40.5 ms | 52 ms | 85 ms |
| upload | 90.7 ms | 382 ms | 800 ms |

### Ranking 1: cost (base run, 20 min)

**Request-serving services**, CPU per request they served:

| Service | CPU-s total | requests | CPU per request | RSS (rest / peak) |
|---|---|---|---|---|
| permission-service | 636 | 145,718 | 4.4 ms | 85 / 93 MB |
| document-service | 557 | 91,574 | 6.1 ms | 113 / 135 MB |
| gateway-service | 296 | 74,429 | 4.0 ms | 143 / 143 MB |
| folder-service | 290 | 53,525 | 5.4 ms | 93 / 96 MB |
| storage-service | 198 | 50,502 | 3.9 ms | 193 / 195 MB |

**Event-driven services**, per upload (14,569 uploads); their work follows `document.*` events, not
requests:

| Service | CPU-s total | CPU per upload |
|---|---|---|
| rendering-service | 1,223 | 84 ms (thumbnails, conversions; native libraries do the work) |
| audit-service | 345 | 24 ms (hash-chained ledger entries) |
| ocr-service | 252 | 17 ms |
| search-service | 226 | 16 ms |
| virus-scan-service | 89 | 6 ms |

Postgres used 373 CPU-s and NATS 116. RAM is not a differentiator: every Python service sits between 85
and 195 MB and barely grows under load. Only rendering-service doubles, to 310 MB.

### Ranking 2: latency share (base run, own time as a share of end-to-end, 500 traces, mean 46 ms)

| Service | own | DB | DB of its work | own per call | calls per request |
|---|---|---|---|---|---|
| permission-service | 25.1 % | 5.4 % | 18 % | 7.8 ms | 1.5 |
| document-service | 14.3 % | 2.5 % | 15 % | 15.1 ms | 0.44 |
| gateway-service | 8.8 % | – | – | 4.1 ms | 1.0 |
| folder-service | 5.9 % | 1.2 % | 18 % | 4.8 ms | 0.57 |
| storage-service | 4.4 % | 0.5 % | 10 % | 4.6 ms | 0.44 |
| virus-scan-service | 3.1 % | 0.4 % | 12 % | 6.3 ms | 0.23 |
| search-service | 1.3 % | 0.8 % | 38 % | 2.7 ms | 0.21 |

**Reading:**
- **permission-service leads both lists.** Every user request reaches it 1.5 times on average, and its
  own time doubles under load (3.1 ms idle in P94-S1, 7.8 ms here) - queueing in its single event loop.
- **The database is not the bottleneck in the healthy run.** It is 15-18 % of the busy services' work,
  except search-service (38 %, full-text queries).

### Where the CPU goes inside a service (py-spy)

| Layer, share of samples | folder-service (base) | permission-service (extended) |
|---|---|---|
| database driver (SQLAlchemy/asyncpg) | 20 % | 58 % |
| HTTP client (httpx) | 16 % | 1 % |
| routing/ASGI | 7 % | 4 % |
| our middleware: sensors, access log, tracing | 1–2 % together | 1–2 % together |
| idle event loop | ~39 % | ~17 % |

- **The middleware is not the per-hop cost** that P94-S1 asked about; the cost is request handling,
  serialisation and the drivers.
- **permission-service spends 3–4 % in HMAC.** That is SCRAM authentication of fresh database
  connections: with the pool budget of ADR 0254 (10 + 20 for permission-service; an earlier version of
  this report said 2 + 3, the default for the other services), the overflow connections are closed after
  use and reopened under concurrency. P95-S1d (ADR 0263) keeps more connections open.

## Extended run: collapse, and why

Goodput 26 %, 17 % errors. Median latencies:
- workflow actions 23–30 s, ending at the gateway's 30-s timeout (`502`);
- **every** other action 2–5 s, including those that never touch workflow-service (list_folder 2.3 s
  instead of 20 ms).

Four causes, each confirmed:

1. **workflow-service `GET /tasks` scans every running instance.**
   - Per instance it reads the claims and the instance, deserialises the workflow, and (since P93-S3)
     reads the route. `limit` applies only after the full scan.
   - With 2,259 running instances before the run (1,046 open `ocr_review` instances since September,
     ~1,200 from case-service tests) plus the 1,059 the run started, one call made thousands of queries.
   - In the traces, 94 % of its time is database time: 16.9 s per call, mostly waiting for one of its 5
     pooled connections. CPU stood at 1.0 core, the ceiling of one Python process. The SLA poll loop
     scans the same instances.
2. **The gateway shares one HTTP connection pool across all upstreams.** When workflow requests hold
   connections for 30 s, everything else waits for a free connection. In a slow list_folder trace the
   gateway's client span took 4.9 s (17.6 s in another), but folder-service received the request only
   after 4,960 ms and answered in 8 ms. The gateway itself used 0.22 cores; it was waiting, not working.
3. **The gateway's 5-s caches have no single-flight.** These are the registry resolution and the
   maintenance-mode state. When an entry expires, every request in flight fetches it again, through the
   same starved pool. Under the extended load, 48 % of the gateway's samples were in those two lookups.
4. **mail-connector `GET /inbound` has no paging.** It returns all 1,641 messages, each with two
   follow-up queries for attachments and routing log: 343 ms CPU per request.

Two more defects surfaced:
- **case-service leaves a case behind when workflow-service's start times out.** P93-S3 handled only the
  `422`. 519 of the run's 623 cases have no process instance, and the callers got a `500`.
- **ocr-service crashed at startup when permission-service was briefly unreachable.** Its review-workflow
  bootstrap had no retry, and with no restart policy it stayed down: the regression had stopped
  permission-service at that moment. **Fixed in this session:** the bootstrap retries with backoff for
  about 2 minutes. Verified by keeping permission-service down during a restart: 5 retries, then a
  normal start.

## Cold permission cache (found in P92-S4)

A folder with 1,000 children, listed by 5 principals:

| Case | median | max |
|---|---|---|
| first listing (cold) | 3,744 ms | 3,924 ms |
| second listing (warm) | 62 ms | – |
| the same principals after one unrelated role assignment | 3,506 ms | – |

Any permission change anywhere bumps the cache generation (ADR 0239), so afterwards every user pays the
cold cost again on a large folder.

## What follows

**For the Rust question (P95-S2):**
- **The biggest problems are algorithmic, and a rewrite would carry them along unchanged.** These are:
  - the workflow scan and the mail inbox (O(N) with N+1 queries);
  - the gateway's shared pool and its cache stampede;
  - the global cache invalidation.
- **Where own compute dominates in the healthy run:**
  - **permission-service:** first in CPU among the request services and first in latency share;
    CPU-bound recomputation, on 1.5 calls per user request;
  - **gateway-service:** in every request, thin logic, 4 ms CPU each.

  These two remain the candidates the plan named.
- **Not candidates:**
  - rendering-service is the largest CPU consumer, but its work is document conversion in native
    libraries;
  - document- and folder-service spend their time in drivers and calls to others.

**Fixes before the rewrite decision** - scheduled as P95-S1a–e (user decision 2026-11-24), with a
second measurement in P95-S1e as the basis for P95-S2:
1. **gateway:** a connection limit per upstream service (bulkhead) and single-flight for its caches.
2. **workflow-service:**
   - a ready-task index maintained on every state change, so the scan goes away (and the SLA loop
     reads the same index);
   - `limit` applied in the query.
3. **mail-connector:** paging for `/inbound`, attachments and routing log in one query each.
4. **case-service:** remove the case, or mark it failed, when the workflow start fails for any reason,
   not only `422`.
5. **permission cache:**
   - invalidation scoped to what a change can affect instead of a global generation bump;
   - computing siblings together for a large listing.
6. **database pool:** revisit ADR 0254's 2 + 3 for the hot services; overflow churn costs SCRAM
   handshakes.
7. **dev-stack debris:** 1,046 open `ocr_review` instances, ~1,200 case-service-test instances and 519
   orphan cases. The 1,059 circulation instances this run started were set to `completed` afterwards
   (status only, history kept), so the dev stack isn't slowed by them.

## Second measurement (P95-S1e, 2026-11-25)

The same profile, tools and settings as above:
- both k6 scenarios, tracing at 10 %, `rank_services.py` with 500 traces;
- py-spy after 690 s in each hold phase;
- the cold-cache measurement.

New in `loadtest/`: `summarize_run.py`, the per-action numbers from a run's k6 JSON. It reproduces the
first run's table above exactly. Results: `loadtest/results/summary-*-20261125.json` and
`ranking-*-20261125.json` (gitignored).

**Starting state:**
- **Before (P95-S1):** 2,259 running instances (test debris) and 519 orphan cases.
- **Now:** 17 running instances, no orphans, cleaned up in P95-S1c.

### Base run

74,330 requests, 62/s, no errors. Goodput 100.00 % (before 99.93 %).

| Action | p50 / p95 / p99 before (ms) | after |
|---|---|---|
| search | 9 / 30 / 66 | 7 / 25 / 52 |
| list_folder | 20 / 102 / 292 | 16 / 39 / 81 |
| download | 34 / 180 / 359 | 26 / 65 / 123 |
| login | 40 / 52 / 82 | 37 / 47 / 86 |
| upload | 91 / 382 / 798 | 74 / 158 / 241 |

The tails come down the most: p95 and p99 drop by 2–3×.

| Service | CPU per request before | after | own time per call before | after |
|---|---|---|---|---|
| permission-service | 4.4 ms | 3.7 ms | 7.8 ms | 5.1 ms |
| document-service | 6.1 ms | 5.0 ms | 15.1 ms | 10.7 ms |
| folder-service | 5.4 ms | 4.5 ms | 4.8 ms | 3.6 ms |
| gateway-service | 4.0 ms | 3.3 ms | 4.1 ms | 3.1 ms |
| storage-service | 3.9 ms | 3.3 ms | 4.6 ms | 4.1 ms |

The mean end-to-end time of the sampled requests is 31 ms (before 46 ms). Latency share: permission-service
22.6 % (before 25.1 %), document-service 15.0 %, gateway-service 9.9 %. **The order is unchanged.**

### Extended run: no collapse

70,044 requests (before 11,336), 58/s, errors 0.39 % (before 16.9 %), goodput 79 % (before 26 %).

| Action | p50 / p95 / p99 before (ms) | after |
|---|---|---|
| list_folder | 2,269 / 14,112 / 17,481 | 27 / 359 / 617 |
| download | 2,419 / 13,408 / 18,510 | 36 / 361 / 571 |
| search | 2,747 / 15,192 / 18,107 | 8 / 23 / 102 |
| upload | 2,492 / 12,849 / 17,692 | 113 / 891 / 1,493 |
| mail_inbound | 4,887 / 15,554 / 20,622 | 14 / 32 / 87 |
| case_list | 3,106 / 14,399 / 18,312 | 326 / 1,198 / 1,682 |
| case_create | 12,180 / 22,409 / 27,078 | 1,370 / 2,773 / 3,610 |
| workflow_start | 26,238 / 39,401 / 46,335 | 1,101 / 2,689 / 3,369 |
| workflow_my_tasks | 30,045 / 40,223 / 47,980 | 1,240 / 2,902 / 3,502 |
| workflow_complete | 30,128 / 39,427 / 48,239 | 1,952 / 4,512 / 5,495 |

The errors are almost all the gateway's fail-fast `503` for workflow-service: 300 of 303, and
`gateway_upstream_refused_total{upstream="workflow-service"}` is 302. A slow service no longer drags the
others down.

### Each fix, as measured

| Fix | Effect |
|---|---|
| **S1a gateway:** bulkhead, single-flight (ADR 0262) | Registry and maintenance lookups: 48 % of the gateway's samples under extended load before, 0 now. Actions that never touch workflow-service are back to base-run latencies under extended load (list_folder p50 27 ms instead of 2.3 s). |
| **S1b workflow:** ready-task index | `GET /tasks` no longer scans the instances: workflow_my_tasks 30 s → 1.2 s. The SLA tick reads no instances without a timer. |
| **S1c:** mail paging, orphan cases, debris | mail_inbound 4.9 s → 14 ms. No orphan cases (before, 519 from one run). |
| **S1d:** permission cache, pool (ADR 0263) | Cold 1,000-child listing 3,744 → 285 ms (192 ms in P95-S1d). After an unrelated grant it stays warm (57 ms instead of 3,506 ms). HMAC in permission-service 3–4 % → 0.2 % (base) and 2.3 % (extended, where its pool reached 30). |

### What is still slow, and why

**workflow-service waits for database connections.**
- It ran with the default pool of 2 + 3.
- The pool sensor showed it full for the whole extended run, and 75 % of its request time was pool
  checkout (the instrumentation's `connect` span).
- case-service (2 + 3) hit its limit at peaks too.
- **Done in this session:** workflow-service 8 + 4, case-service 4 + 4 (compose and Helm, same budget rule).

**A second extended run** after that change (1795594840–1795596048) was worse: goodput 54 %,
workflow_my_tasks p50 3.0 s, case_list 2.5 s, list_folder 427 ms.
- **Cause:** it started on the leftovers of the first run, not on the pool change. The first run left
  3,100 running circulations and about 3,000 open cases of its test users. k6's teardown removes users
  and the folder, not these.
- **It is not comparable,** and the pool change's own effect remains unmeasured.
- **What it did show:** two paths that grow with that state.
  1. **`GET /tasks?eligible=me`:** SQL keeps unclaimed tasks and the caller's own claims. Lane and route
     eligibility are checked in Python while the index is read in chunks. With thousands of tasks
     assigned to other principals, every "Meine Aufgaben" call reads all of them again (the limitation
     P95-S1b recorded). **Fix:** store each ready task's responsible principal, group or role in the
     index and filter in SQL. That needs a sync on route changes and lane-binding changes.
  2. **`GET /cases`:** not paged. It returns every open case, 5,822 after the two runs.
- **Cleaned up afterwards:** the 6,336 load-test circulations were terminated. The open load-test cases
  remain, because cases cannot be deleted.

**rendering-service falls behind under load.**
- Its `document.>` consumer runs one message at a time, at 100 % of one core.
- After the runs of P95-S1 and P95-S1e it was 229,819 events behind, finishing about one per second
  against about 12 uploads per second under load.
- The regression's two rendering integration tests timed out because of it: their event was at the end of
  that queue.
- **Fixed for the dev stack:** the consumer was moved to the end of the stream. The skipped events were
  almost all load-test uploads, already trashed by k6.
- The throughput itself is P95-S1f.
- It is the one service whose own (native) work doesn't keep pace. It is still no Rust candidate: the
  work happens in the conversion libraries.

**Scheduled as P95-S1f** (user decision 2026-11-25): `eligible=me` in SQL, `GET /cases` paged, rendering
throughput, the k6 teardown terminating its circulations, and two extended runs back to back.

### For the Rust question (P95-S2)

- **The algorithmic and configuration problems of the first measurement are fixed and measured.** What
  remains slow in the extended scenario is the two O(N) paths above and pool sizing. That is again
  algorithmic or configuration, not compute.
- **Where own compute dominates in the healthy base run is unchanged:**
  - permission-service: first in latency share, 3.7 ms CPU per request, 1.37 calls per request;
  - gateway-service: 3.3 ms CPU per request.
  
  Both are now measured without the queueing and the cache invalidation that inflated them before.
- **The layers inside permission-service:** about half of the samples are in the database driver
  (SQLAlchemy/asyncpg). A rewrite would replace that layer as well.

## Third measurement (P95-S1f, 2026-11-25)

**Settings:** two extended runs back to back (1795601438–1795602761 and 1795602881–1795604199), same
settings as before. Between them, only the k6 teardown, which since P95-S1f terminates the run's
instances (4,592 and 4,456).

**Fixed before the runs:**
- `GET /tasks?eligible=me` filters in SQL, using who is responsible for each ready task.
- `GET /cases` is paged.
- rendering-service processes 4 renditions in parallel, with Pillow off the event loop.
- workflow-service and case-service have the larger pools from P95-S1e.

| | P95-S1e run 1 | P95-S1f run 1 | P95-S1f run 2 |
|---|---|---|---|
| Requests | 70,044 (58/s) | 89,678 (74/s) | 89,091 (74/s) |
| Errors | 0.39 % | 0.00 % | 0.01 % |
| Goodput | 79 % | 96.4 % | 95.5 % |

| Action, p50 / p95 / p99 (ms) | P95-S1e run 1 | P95-S1f run 1 | P95-S1f run 2 |
|---|---|---|---|
| workflow_my_tasks | 1,240 / 2,902 / 3,502 | 122 / 628 / 988 | 105 / 693 / 1,502 |
| workflow_start | 1,101 / 2,689 / 3,369 | 217 / 810 / 1,136 | 194 / 913 / 2,000 |
| workflow_complete | 1,952 / 4,512 / 5,495 | 314 / 1,269 / 1,866 | 268 / 1,301 / 2,863 |
| case_create | 1,370 / 2,773 / 3,610 | 343 / 1,339 / 2,231 | 333 / 1,584 / 3,439 |
| case_list | 326 / 1,198 / 1,682 | 194 / 1,280 / 2,112 | 181 / 1,485 / 2,581 |
| list_folder | 27 / 359 / 617 | 62 / 538 / 861 | 60 / 598 / 1,096 |
| download | 36 / 361 / 571 | 82 / 553 / 898 | 78 / 617 / 1,168 |
| search | 8 / 23 / 102 | 15 / 157 / 403 | 15 / 176 / 557 |
| upload | 113 / 891 / 1,493 | 225 / 1,386 / 2,626 | 221 / 1,612 / 3,136 |
| mail_inbound | 14 / 32 / 87 | 24 / 147 / 306 | 25 / 161 / 454 |

**Reading:**
- **The second run no longer degrades.** p50 is the same or better, p99 somewhat higher. In P95-S1e the
  second run fell to 54 % goodput on the first run's leftovers.
- **The workflow and case paths are 4–10× faster at p50.** "Meine Aufgaben" no longer reads other
  people's tasks, and workflow-service no longer waits for its pool: its DB share of its own work is
  27–29 %, against 81 % in P95-S1e.
- **The bottleneck has moved to permission-service.** The run pushes about a quarter more requests through
  (89,678 instead of 70,044, and three times as many completed circulations). Everything else now waits
  on permission-service:
  - its own time per call went from 12.8 to 53–64 ms;
  - its latency share is 28–33 %;
  - it reached the maximum of 30 pooled connections.
  
  So list_folder, download and search are slower at p50 than in P95-S1e, while still in the tens of
  milliseconds. Its database time per call (19–36 ms) is part of it, but most is its own time in one busy
  event loop.
- **rendering-service still fell behind during the runs:** backlog 28,403 after run 1, 60,553 after run 2.
  The four parallel conversions each built a fresh LibreOffice profile.
  - **Fixed after the runs:** a pool of reused profiles. A conversion takes 200–260 ms instead of about
    960 ms.
  - **Effect, live:** 680–814 renditions per minute (before 3–10) while draining the backlog. That
    covers the load test's upload rate.
  - **Not yet measured under a full load run.**

**For P95-S2:** the algorithmic and configuration problems the measurements found are fixed. They didn't
come back in two consecutive runs. Under the full mixed load, **permission-service's own processing** is
now the limiting factor, the candidate P95-S1 named. gateway-service follows at 7–9 % latency share
(12.9–24.3 ms own time per call).

## Fourth measurement (P95-S2c, 2026-11-25): the Python baseline for ADR 0257

**State:**
- P95-S2a: queue groups;
- P95-S2b: permission-service answers a check with one read, and runs as replicas.

**Four runs,** same settings as before (tracing 10 %, py-spy after 690 s):
- base and extended with permission-service at **1** instance;
- then base and extended at **3** instances (compose profile `permission-replicas`).

`rank_services.py` now adds up a service's replicas (it booked them as a separate service).

**One difference from earlier runs:** rendering-service now keeps up (P95-S1f), so it does its work during
the run. In the base run it used 2.8 cores against 1.0 in P95-S1e, and the stack 6.0–6.3 cores against
3.9. On a shared host that costs the other services latency. The base runs are therefore compared within
this measurement (1 vs 3), not with P95-S1e.

### Base run

| | 1 instance | 3 instances |
|---|---|---|
| Requests / errors / goodput | 73,673 / 0.00 % / 99.84 % | 74,349 / 0.00 % / 99.89 % |

| Action, p50 / p95 / p99 (ms) | 1 instance | 3 instances |
|---|---|---|
| list_folder | 18 / 53 / 124 | 12 / 26 / 61 |
| download | 34 / 141 / 278 | 23 / 69 / 192 |
| search | 9 / 33 / 76 | 7 / 26 / 54 |
| upload | 94 / 293 / 512 | 66 / 182 / 658 |
| login | 41 / 54 / 100 | 35 / 44 / 75 |

### Extended run

| | P95-S1f run 1 | 1 instance | 3 instances |
|---|---|---|---|
| Requests | 89,678 | 101,301 | 102,483 |
| Errors | 0.00 % | 0.00 % | 0.00 % |
| Goodput | 96.4 % | 99.48 % | **100.00 %** |
| Mean e2e of the sampled requests | 178 ms | 51.7 ms | 24.8 ms |

| Action, p50 / p95 / p99 (ms) | P95-S1f run 1 | 1 instance | 3 instances |
|---|---|---|---|
| workflow_my_tasks | 122 / 628 / 988 | 19 / 82 / 422 | 18 / 43 / 92 |
| workflow_start | 217 / 810 / 1,136 | 31 / 158 / 1,529 | 28 / 83 / 163 |
| workflow_complete | 314 / 1,269 / 1,866 | 45 / 195 / 652 | 40 / 114 / 212 |
| case_create | 343 / 1,339 / 2,231 | 62 / 267 / 2,361 | 53 / 132 / 283 |
| case_list | 194 / 1,280 / 2,112 | 50 / 208 / 1,795 | 45 / 90 / 147 |
| list_folder | 62 / 538 / 861 | 13 / 45 / 105 | 12 / 28 / 62 |
| download | 82 / 553 / 898 | 20 / 60 / 247 | 19 / 36 / 74 |
| search | 15 / 157 / 403 | 7 / 15 / 48 | 7 / 15 / 51 |
| upload | 225 / 1,386 / 2,626 | 65 / 251 / 2,127 | 57 / 103 / 163 |
| mail_inbound | 24 / 147 / 306 | 12 / 23 / 71 | 13 / 24 / 75 |

### permission-service

| | P95-S1f (extended) | 1 instance (ext.) | 3 instances (ext.) | 1 instance (base) | 3 instances (base) |
|---|---|---|---|---|---|
| Own time per call | 53–64 ms | 5.1 ms | 3.2 ms | 4.9 ms | 2.6 ms |
| Latency share | 28–33 % | 10.9 % | 14.8 % | 13.6 % | 13.4 % |
| CPU per request, all instances | – | 3.1 ms | 3.6 ms | 3.7 ms | 3.8 ms |
| CPU, average over the run | – | 0.37 cores | 0.43 cores | 0.44 cores | 0.48 cores |
| Share of the stack's CPU | – | 9 % | 10 % | 7 % | 8 % |

**Reading:**
- **The one-read hot path (P95-S2b) removed the bottleneck even with one instance.** Own time per call
  went from 53–64 ms to about 5 ms under the full extended load.
- **Replicas halve the latency again and smooth the tails.** The extended run reaches 100 % goodput with
  every p99 below 300 ms. CPU per request stays the same, so they buy latency and headroom, not
  efficiency.
- **CPU per request in the full mix is 3.1–3.8 ms,** against 1.9 ms for a cached check in isolation.
  The mix also has misses (every upload's new document), resource registrations, batch checks and group
  lookups, which still go through the ORM path. ORM samples are down to 4–5 % (12–13 % before).
- **SQLAlchemy Core/engine is still 35–38 %.** About 10 percentage points of that is building the
  statement on every call (`select`, `in_`, `__eq__`). Building it once with bind parameters is a further
  Python lever, not applied here because this session only measures.
- **The cold-cache measurement:** 254 ms cold, warm after an unrelated grant (38 ms).

### What this means for the Rust question (ADR 0257 §7 and §9)

ADR 0257 §9's rule for the pilot: start it only if permission-service **still limits the full load** and
Rust promises at least 2× in CPU per 1,000 requests. The first condition no longer holds:
- **No longer limiting:** at one instance, permission-service is 11 % of the extended latency
  (workflow-service is the largest, 34 %). With three instances the extended run has no errors, 100 %
  goodput and every p99 below 300 ms.
- **The absolute gain would be small:** permission-service uses 0.37–0.48 cores on average, 7–10 % of the
  stack's CPU. A Rust service several times more efficient would save perhaps 0.3–0.4 cores. rendering-
  service alone uses 1.2–2.8 cores, in native libraries.
- **Cheaper levers remain in Python:** the statement built once (about 10 % of the service's CPU), and
  the miss path without the ORM.
