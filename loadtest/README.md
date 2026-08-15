# Load testing

Load test for the DMS gateway using [k6](https://k6.io/), simulating a mixed,
realistic usage pattern (login, folder browsing, search, upload, download)
against the running stack. Load ramps up over the test duration, response
times are recorded per action, and "goodput" - successful (2xx) responses
that also complete under an SLA latency threshold - is tracked as a first-
class metric. k6 pushes results to Prometheus/Grafana live and to a JSON
file for later analysis in `notebook/analysis.ipynb`. Per-container CPU/
memory (via cadvisor + Prometheus) is captured alongside it, so the client-
observed metrics above can be correlated with the actual resource cost per
service - the basis for service-sizing decisions.

## Layout

- `k6/scenario.js` - the test script (setup/default/teardown, ramp profile,
  goodput metric).
- `install-k6.sh` - downloads a pinned k6 binary into `.tools/` (gitignored,
  no root/system install needed).
- `run.sh` - runs the test with both outputs wired up.
- `results/` - JSON output per run (gitignored - regenerate by running).
- `notebook/analysis.ipynb` - loads the latest `results/run-*.json`, plots
  the ramp profile/throughput/response times/goodput, and pulls per-service
  CPU/memory for the same time window live from Prometheus.

## Prerequisites

- The DMS stack running locally (`docker compose up` in `infra/`), including:
  - Prometheus with `--web.enable-remote-write-receiver` (already set in
    `infra/docker-compose.yml`) and scraping `cadvisor` (`infra/prometheus.yml`).
  - `cadvisor`, for per-container CPU/memory/network (`infra/docker-compose.yml`).
  - Grafana.
- A bootstrapped admin technical account (`users-admin` by default) able to
  create and delete users - used by `setup()`/`teardown()` to provision and
  clean up an isolated pool of test accounts.
- For local dev only: the license-service document/user limits can block a
  full-scale run (see "License limits" below).

```bash
loadtest/install-k6.sh
```

## Running

```bash
loadtest/run.sh
```

This runs the full profile: 10 minutes ramp-up to 200 VUs, 5 minutes hold,
5 minutes ramp-down (~20 minutes total). Useful overrides:

```bash
# Shorter rehearsal before committing to a full run
RAMP_UP=1m RAMP_UP_VUS=20 HOLD=30s RAMP_DOWN=30s loadtest/run.sh

# Different SLA threshold for the goodput metric (default: 1000ms)
SLA_MS=500 loadtest/run.sh

# Different gateway / admin account
GATEWAY_BASE_URL=http://localhost:8009 ADMIN_USERNAME=users-admin ADMIN_PASSWORD=users-admin loadtest/run.sh
```

Each run creates its own pool of `loadtest-<runId>-*` test users and a
dedicated `Load Test <runId>` folder under root (kept separate from real
data and from other services' pre-existing root-folder content, so
measurements aren't confounded by unrelated navigation). `teardown()` trashes
that folder (cascades to every document in it, see "Design notes" below) and
deletes all test users - a run leaves no active data behind. If a run is
killed outright (not even a graceful stop) before teardown, the
`loadtest-*` users and `Load Test *` folder must be cleaned up manually via
the auth-service/folder-service admin APIs.

## License limits

`license-service` enforces the installed license's document/user limits
(`GET /api/license-service/license/status`) - a full-scale run can create
tens of thousands of documents, which will hit a low dev-license document
cap (uploads then fail with `403`, everything else stays healthy). For local
development only, install an unlimited license: mint a token with
`max_users`/`storage_limit_gb`/`document_limit` all `null` using the dev
signing key at `services/license-service/tests/fixtures/dev_private_key.pem`
(see `services/license-service/tests/fixtures/license_factory.py` for the
claim shape), grant the `domain-admin-license` role to your admin principal
via `POST /api/permission-service/role-assignments`, then `POST` the token
to `/license`. This is local Postgres state only, nothing to commit.

## Watching it live

- **DMS Load Test (k6)** dashboard in Grafana
  (`http://localhost:<GRAFANA_PORT>/d/dms-loadtest-k6`, provisioned
  automatically) while `run.sh` is running: virtual users (ramp shape),
  requests/sec, p99 response time per action, and goodput vs. error rate.
- **DMS Service Resource Usage** dashboard
  (`http://localhost:<GRAFANA_PORT>/d/dms-resource-usage`): per-service
  CPU/memory/network from cadvisor - watch this alongside the k6 dashboard
  to see which services are under the most load in real time.

## Analyzing results

The notebook's dependencies (pandas/matplotlib/jupyter/requests/ipykernel)
live in the repo's own workspace venv, under the `notebook` dependency
group - no separate venv needed:

```bash
uv sync --group notebook   # first time only, or after the group changes
.venv/bin/jupyter notebook loadtest/notebook/analysis.ipynb
```

The notebook reads the most recent `results/run-*.json` by default; point it
at an older run by editing `RESULT_FILE` in the first code cell. Keep its
`SLA_MS` constant in sync with whatever `SLA_MS` the analyzed run used. The
resource-usage section queries Prometheus directly (`PROMETHEUS_URL`,
default `http://localhost:9090`) for the run's time window - it needs
Prometheus to have been scraping cadvisor during the run and to still have
that data (default retention: 15 days); older runs predating the cadvisor
setup will show an empty resource section.

## Design notes

- **Per-VU token caching**: each k6 VU logs in once and reuses the token
  (refreshed near expiry) instead of logging in every iteration. The gateway
  rate limiter keys pre-auth requests (including `/login`) by client IP, not
  by user - with up to 200 concurrent VUs on one machine, logging in every
  iteration would risk tripping that limit for reasons unrelated to the
  actual workload being measured.
- **Action-level Prometheus tags**: every request passes an explicit `name`
  tag (`login`, `list_folder`, `search`, `download`, `upload`, plus a few
  `setup_*`/`teardown_*` tags). Without this, k6's Prometheus output defaults
  to using the full request URL as the tag, which explodes cardinality for
  parameterized paths (document/user IDs in the URL).
- **Goodput definition**: a request counts toward goodput only if it is both
  2xx **and** faster than `SLA_MS`. This is stricter than k6's built-in
  `http_req_failed` (which only tracks non-2xx/connection failures) -
  goodput can drop even while the error rate stays at zero, if requests are
  succeeding but too slowly under load.
- **Teardown re-authenticates and uses a single cascading trash call**: a
  full run takes ~20 minutes, well past the auth-service access token
  lifetime (300s default) - `teardown()` re-logs in as needed rather than
  reusing the `setup()` token, and retries on `429` (cleanup itself can
  exceed the gateway's per-principal rate limit). It also trashes the whole
  test folder in one `POST /folders/{id}/trash` call instead of listing and
  individually `DELETE`ing every document - folder-service cascades the
  trash over the folder's full active subtree server-side. At full-scale
  upload volume (tens of thousands of documents), one-by-one deletion blows
  past k6's default 60s `teardownTimeout` and the per-admin rate limit; the
  single cascading call finishes in a couple of seconds regardless of volume.
- **cadvisor is pinned to v0.52.1, not the "latest stable" v0.49.x line**:
  v0.49.1's embedded Docker client only speaks API 1.41, which recent Docker
  daemons (tested against 29.1.3, minimum supported API 1.44) reject
  outright - cadvisor silently falls back to raw cgroup stats with no
  container name/label enrichment (metrics exist but every series is
  unlabeled). v0.52.1 speaks a compatible API version.
