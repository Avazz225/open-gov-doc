from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    """Gemeinsame Konfigurationsbasis. Jeder Service leitet hiervon ab und
    überschreibt mindestens ``service_name`` mit einem festen Default.
    """

    model_config = SettingsConfigDict(env_prefix="DMS_", env_file=".env", extra="ignore")

    service_name: str
    environment: str = "development"
    log_level: str = "INFO"

    postgres_dsn: str | None = None
    nats_url: str = "nats://localhost:4222"
    otel_exporter_endpoint: str | None = None

    # Selbst-Registrierung bei der Registry (3.2a, seit P4-S1). Beide Felder
    # bleiben standardmäßig leer - Discovery ist ein Zusatznutzen für Services,
    # die vom Gateway geroutet werden, kein Hard-Dependency. Erst wenn beide
    # gesetzt sind, meldet sich ein Service über dms-registry-client an.
    registry_service_base_url: str | None = None
    self_address: str | None = None

    # Installations-Identität (3a, P13-S1): EIN gemeinsamer Wert für die
    # gesamte Installation, über dieselben zwei Umgebungsvariablen
    # (`DMS_INSTALLATION_ID`/`DMS_INSTALLATION_DISPLAY_NAME`) an jeden Service
    # des Stacks weitergereicht - ersetzt die zuvor uneinheitliche Praxis
    # (nur `workflow-service` kannte bislang eine eigene, isoliert
    # konfigurierte `installation_display_name`, jeder andere Service kannte
    # gar keine Installationskennung). `installation_id` ist bewusst NICHT
    # dieselbe Kennung, mit der sich `workflow-service` beim Federation Hub
    # anmeldet (7.4) - jene bleibt absichtlich eine eigene, zufällig erzeugte,
    # opt-in/pseudonyme Kennung (siehe `federation_client.py`), damit
    # Föderationspartner nicht automatisch die interne Fleet-/Lizenz-Identität
    # dieser Installation erfahren.
    installation_id: str = "local-dev"
    installation_display_name: str = "DMS-Installation (Entwicklung)"
