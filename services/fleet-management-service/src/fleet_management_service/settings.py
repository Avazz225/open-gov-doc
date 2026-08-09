from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    """Der Fleet-/Lizenz-Management-Service (3a) ist bewusst **kein** interner
    Service einer einzelnen Installation - ein Betreiber, der mehrere
    Installationen überblickt, betreibt ihn unabhängig davon (gleiches
    Architekturmuster wie `federation-hub-service`, ADR 0028). Er registriert
    sich deshalb nicht bei einer `registry-service`
    (`BaseServiceSettings.registry_service_base_url`/`self_address` bleiben
    ungenutzt) und hat keinen Event-Bus-Producer/-Consumer. Für lokale
    Entwicklung/Tests wird er trotzdem in `infra/docker-compose.yml`
    mitgeliefert (dev-only Convenience) - ein Betreiber würde ihn in
    Produktion separat betreiben, siehe docs/services/fleet-management-service.md."""

    service_name: str = "fleet-management-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Timeout für ausgehende Aufrufe an eine verwaltete Installation über
    # deren Gateway (Status-Abfrage/Lizenz-Push/Provisionierung) - großzügiger
    # als reine Healthchecks, da `POST .../config/import` mehrere
    # Owner-Service-Aufrufe hintereinander ausführt (siehe config-service).
    agent_request_timeout_seconds: float = 30.0
