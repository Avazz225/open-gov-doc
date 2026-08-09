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

    # Betreiber-Geheimnis für `POST /installations/{id}/revoke` (P13-S4,
    # ADR 0039) - bewusst unabhängig von jeder Installation: Revocation ist
    # explizit für den Fall gedacht, dass eine Installation selbst nicht mehr
    # vertrauenswürdig signieren kann (kompromittierter Schlüssel), kann also
    # nicht an deren eigene Signatur gebunden sein. `None` (Default) sperrt
    # den Endpunkt vollständig - ein Hub-Betreiber muss diesen Wert bewusst
    # setzen, um Revocation überhaupt zu aktivieren.
    hub_operator_key: str | None = None
