# dms-common

Geteilte technische Basis für alle DMS-Services (keine Fachlogik):

- `settings.BaseServiceSettings` — pydantic-settings-Basis (Env-Prefix `DMS_`); jeder Service leitet ab und setzt `service_name`.
- `logging.configure_logging(settings)` — strukturiertes JSON-Logging nach stdout, reichert jeden Eintrag automatisch mit `service_name`/`environment` an.
- `otel.configure_tracing(settings, exporter)` — globaler TracerProvider; der Exporter (Console lokal, OTLP ab Phase 11) wird bewusst vom Aufrufer übergeben.

## Nutzung

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
