# dms-common

Shared technical foundation for all DMS services (no business logic):

- `settings.BaseServiceSettings` — pydantic-settings base (env prefix `DMS_`); each service derives from it and sets `service_name`. Since P13-S1 also `installation_id`/`installation_display_name` (3a) — a shared value for the entire installation (`DMS_INSTALLATION_ID`/`DMS_INSTALLATION_DISPLAY_NAME`), e.g. exposed by `registry-service`'s `GET /installation` and used by `license-service` for license binding.
- `logging.configure_logging(settings)` — structured JSON logging to stdout, automatically enriches each entry with `service_name`/`environment`.
- `otel.configure_tracing(settings, exporter)` — global TracerProvider; the exporter (console locally, OTLP from Phase 11 onward) is deliberately passed in by the caller.

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
