# Per-service performance ranking (P95-S1, 2026-11-24)

The input for P95-S2, the scoping of a Rust pilot (ADR 0257). It asks where the services spend time and
resources under load, and which of that a faster implementation could fix and which needs a
different algorithm.

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
  connections: with the pool budget of ADR 0254 (2 + 3 overflow), the overflow connections are closed
  after use and reopened under concurrency.

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

**Fixes worth doing before or instead of a rewrite** (proposed, not scheduled):
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
