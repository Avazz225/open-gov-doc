from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "workflow-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # SLA-Zeitüberwachung (P6-S2, ADR 0020): Poll-Intervall der Boundary-Timer-Prüfung.
    # Begrenzt die Erkennungspräzision einer SLA-Überschreitung auf dieses Intervall -
    # kein Push-Mechanismus vorhanden, siehe ADR 0020.
    sla_poll_interval_seconds: int = 30

    # Retrofit P6-S6 (4.8/Autorisierung): Prozessdefinitionen (BPMN-/Script-
    # Task-Upload) verlangen die Domain-Admin-Capability `admin.object_config`;
    # der SLA-Poll-Loop überspringt seinen Tick während aktivem Wartungsmodus.
    permission_service_base_url: str = "http://localhost:8004"
