from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    """Der Federation Hub (7.4) ist bewusst **kein** interner Service einer
    Installation - er registriert sich deshalb nicht bei einer `registry-service`
    (`BaseServiceSettings.registry_service_base_url`/`self_address` bleiben
    ungenutzt) und hat auch keinen Event-Bus-Producer/-Consumer (er protokolliert
    Vermittlungs-Metadaten nur in seiner eigenen `handover`-Tabelle, siehe
    `docs/services/federation-hub-service.md`). Für lokale Entwicklung/Tests wird
    er trotzdem in `infra/docker-compose.yml` mitgeliefert (dev-only Convenience,
    siehe ADR 0028) - ein Betreiber würde ihn in Produktion separat betreiben."""

    service_name: str = "federation-hub-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"
