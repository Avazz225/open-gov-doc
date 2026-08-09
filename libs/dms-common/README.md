# dms-common

Geteilte technische Basis für alle DMS-Services (keine Fachlogik):

- `settings.BaseServiceSettings` — pydantic-settings-Basis (Env-Prefix `DMS_`); jeder Service leitet ab und setzt `service_name`. Seit P13-S1 zusätzlich `installation_id`/`installation_display_name` (3a) — ein gemeinsamer Wert für die gesamte Installation (`DMS_INSTALLATION_ID`/`DMS_INSTALLATION_DISPLAY_NAME`), z. B. von `registry-service`s `GET /installation` exponiert und von `license-service` zur Lizenzbindung genutzt.
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
