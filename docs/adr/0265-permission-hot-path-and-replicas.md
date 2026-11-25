# 0265 — permission-service: one read per check, and replicas

**Status:** accepted
**Context:** P95-S2b, the second Python lever of ADR 0257 §3. ADR 0257 found:
- permission-service limits the full load (P95-S1f: 53–64 ms own time per call);
- about 41 % of its CPU is SQLAlchemy around many small queries;
- a second process was impossible, which P95-S2a fixed with queue groups (ADR 0264).

**Measured on the dev stack** with k6 against permission-service directly:
- `GET /check` (cache hit, a document 3 levels deep) and `POST /check/batch` (50 documents);
- 25 or 50 virtual users, 20 s each;
- CPU from the container cgroups.

A first bench with a Python client was limited by the client itself; k6 is not.

## Decision 1 — The hot path reads once, outside a transaction

**Before,** a warm `GET /check` made about a dozen round trips:
- the pool's pre-ping, then BEGIN;
- the two cache generations;
- the cached row as an ORM object;
- the scope-lock walk, two queries per ancestor level (it ran on every check, although a lock is rarely
  active);
- COMMIT, and the pool's reset on return.

**Now** `repository.cached_effective_permissions` answers the usual case with **one SQLAlchemy Core
statement in autocommit**. It returns:
- the global and the principal's generation;
- the cached rows of the requested resources that match both;
- whether any scope lock is active.

Rows come back as plain dataclasses, not ORM objects, and the hit sensor counts once per read.

**Unchanged:**
- A miss goes through `get_effective_permissions`/`get_effective_permissions_batch` as before. So does
  the validity rule of ADR 0239/0263, and so do their race tests, which pass without a change.
- A row is a hit only while both generations match. The lookup compares them in the same snapshot it
  reads the row in.
- The lock walk runs only when a lock is active.

**`/check`, `/check/batch` and `/effective-permissions`** use the lookup. Every other caller (capability
checks inside permission-service's own write endpoints) keeps the session path.

| Single process, 25 VUs | before | after |
|---|---|---|
| `GET /check` | 311/s, 3.2 ms CPU, p50 ~70–90 ms | 488–528/s, 1.9–2.0 ms CPU, p50 41–44 ms |
| `POST /check/batch` (50) | 317/s, 3.1 ms CPU | 346–361/s, 2.8 ms CPU |

## Decision 2 — Replicas, each registered under its own address

**Why replicas, not uvicorn workers.** Several workers in one container would share one port but not
their memory:
- each worker's sensors would answer a scrape at random;
- every worker would register the same address with the registry;
- every worker would run the startup tasks.

Separate instances keep the sensors, the registration and the lifecycle per process, the same as on
Kubernetes.

**What changed:**
- **`{ip}` in `DMS_SELF_ADDRESS`** (`dms-registry-client`, `resolve_self_address`) registers an instance
  under its own IP. This matters because, with several replicas behind one name, monitoring scraped and
  the gateway routed to whichever instance the name resolved to.
  - **Compose:** permission-service uses `http://{ip}:8000`.
  - **Helm:** every service now registers under its pod IP (`DMS_POD_IP` from the downward API, then
    `http://$(DMS_POD_IP):8000`). That was already wrong for any service with `replicas`/`autoscaling` above
    one, gateway-service among them, which has autoscaling on by default.
- **Compose profile `permission-replicas`:** `permission-service-replica`, with the same image and
  configuration, no host port, the network alias `permission-service` (the callers' connections spread
  over every instance), and `PERMISSION_SERVICE_EXTRA_REPLICAS` (default 1). Off by default.
- **No cache invalidation at startup any more:**
  - ADR 0254 added one for denies cached before P89, and every installation has started since.
  - With replicas, each new instance (a scale-out, every pod of a rolling update) would make the whole
    cache cold for everyone.
- **Pool budget unchanged** (20 + 10 per instance). The Helm formula already counts `maxReplicas` ×
  budget. In compose, two extra instances hold up to 40 more idle connections, within
  `max_connections=300`.

**Measured** with k6 inside the stack network, against the name `permission-service`, 50 VUs:

| | 1 instance | 3 instances |
|---|---|---|
| `GET /check` | 437/s, p50 97 ms, p95 206 ms | 1,412/s, p50 28 ms, p95 67 ms |
| `POST /check/batch` (50) | 304/s, p50 151 ms | 997/s, p50 45 ms |
| CPU per request | 2.3 / 3.3 ms | 2.1 / 3.0 ms |

The throughput is 3.2–3.3× higher, and CPU per request is unchanged: the gain is parallelism, as
expected.

**Checked:**
- three instances registered with their own IPs and deregistered on stop;
- monitoring scraped each separately (three `exported_instance` values, each with its pool of 20);
- the NATS consumers shared through the queue group (ADR 0264).

## Consequences

- **For ADR 0257:** permission-service now has a Python baseline with both levers. Per process it costs
  1.9–2.0 ms CPU per check (from 3.2) and scales with instances. P95-S2c measures the full mix on it, and
  the Rust criterion (≥ 2× CPU per 1,000 requests at equal p95) is judged against that.
- **Helm:** the address change affects every service's registration (now one entry per pod). The
  gateway's `InstanceResolver` and monitoring work with any number of instances per type.
- **A cold check is unchanged.** It still computes through the ORM path, which the batch computation of
  ADR 0263 already reduced.
