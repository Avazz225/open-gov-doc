from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "registry-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Konfigurierbares Intervall (3.2a): eine Instanz gilt als ausgefallen, wenn
    # sie länger als heartbeat_timeout_seconds kein Heartbeat mehr gesendet hat.
    heartbeat_timeout_seconds: float = 15.0

    # Lizenzvermittlung (Konzept 3.2b/9.3, P9-S2). Nur `service_type`-Werte,
    # die hier gelistet sind, gelten als separat lizenzierbare
    # "Applikationskomponenten" (9.1) - jeder andere Service bleibt "core"
    # und bekommt immer license_status="licensed". Policy je Komponente:
    # "demo" (nur Lesezugriff) oder "lock" (vollstaendige Sperre).
    # `webdav-connector` (P12-S1) ist der erste Connector, der diesem Muster
    # folgt - Konzept 3.3 nennt Connectoren woertlich als Beispiel fuer
    # lizenzierbare Komponenten.
    license_service_base_url: str = "http://localhost:8023"
    license_status_cache_ttl_seconds: float = 60.0
    licensable_components: dict[str, str] = {
        "workflow-service": "demo",
        "webdav-connector": "demo",
    }

    # Sensor-Konzept (10.1, P11-S1): registry-service ist selbst einer der
    # zwei Piloten (kein Vollretrofit, siehe P11-S0-Befund). Aktivierungs-
    # status kommt von `monitoring-service`, nicht von hier selbst.
    monitoring_service_base_url: str = "http://localhost:8026"
    sensor_sample_interval_seconds: float = 15.0
