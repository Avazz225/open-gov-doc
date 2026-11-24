# dms-common

Shared technical foundation for all DMS services (no business logic):

- `settings.BaseServiceSettings` — pydantic-settings base (env prefix `DMS_`); each service derives from it and sets `service_name`. Since P13-S1 also `installation_id`/`installation_display_name` (3a) — a shared value for the entire installation (`DMS_INSTALLATION_ID`/`DMS_INSTALLATION_DISPLAY_NAME`), e.g. exposed by `registry-service`'s `GET /installation` and used by `license-service` for license binding.
- `logging.configure_logging(settings)` — structured JSON logging to stdout, automatically enriches each entry with `service_name`/`environment`. Since P94-S2 ([ADR 0251](../../docs/adr/0251-central-log-aggregation.md)) also `trace_id`/`span_id` when the line is written inside a sampled trace, and uvicorn's loggers go through the same JSON handler (its access log is replaced, see below).
- `access_log.AccessLogMiddleware` (P94-S2) — one JSON line per request (`dms.access`: `http_method`, `http_route`, `http_path`, `http_status`, `duration_ms`; WARNING from status 500), written inside the request so it carries the trace id; `/healthz` and `/metrics` are skipped.
- `otel.setup_observability(app, settings)` (P94-S2) — the call every service makes right after `app = FastAPI(...)`: the access-log middleware, then `setup_tracing`.
- `otel.setup_tracing(app, settings)` (P94-S1, [ADR 0250](../../docs/adr/0250-distributed-tracing.md)) — called by every service right after `app = FastAPI(...)`. A no-op without `DMS_OTEL_EXPORTER_ENDPOINT`; with it: OTLP/HTTP export, FastAPI/httpx/SQLAlchemy instrumentation, a parent-based ratio sampler (`DMS_OTEL_SAMPLING_RATIO`, then monitoring-service's value via `dms_metrics_client`) that starts traces only at entry points. `configure_tracing(settings, exporter)` is the provider part on its own (tests pass an in-memory exporter); `set_sampling_ratio(ratio)` changes the ratio at runtime.

## Usage

```python
from dms_common import BaseServiceSettings, configure_logging


class Settings(BaseServiceSettings):
    service_name: str = "registry-service"


settings = Settings()
configure_logging(settings)
```

## Tests

```bash
uv run --package dms-common pytest libs/dms-common/tests
```
