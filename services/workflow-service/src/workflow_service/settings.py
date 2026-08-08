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

    # Signature Task (3.10, P6-S7): `POST .../tasks/{id}/complete` verlangt bei
    # einer als `taskType=signature` markierten Task eine gültige `signature_id`,
    # siehe `signature_client.py`.
    signature_service_base_url: str = "http://localhost:8017"

    # Federation Hub (7.4, P6-S9): opt-in - bleibt `None`, meldet sich diese
    # Installation nicht am Hub an, bietet der Process Designer föderierte
    # Prozessschritte auch gar nicht erst an (siehe `federation_client.py`).
    federation_hub_base_url: str | None = None
    # Basis-URL, unter der DIESE Installation vom Hub aus über den eigenen
    # Gateway erreichbar ist (nicht die interne Adresse dieses Containers -
    # der Hub liefert über `/api/workflow-service/...`, damit auch die
    # Gateway-JWT-/Wartungsmodus-Logik greift, siehe ADR 0028).
    installation_gateway_base_url: str = "http://gateway-service:8000"
    installation_display_name: str = "DMS-Installation (workflow-service)"
    installation_version: str = "1.0"
    installation_min_compatible_peer_version: str = "1.0"
    # Empfängerseitige Zuordnung `targetProcessType` (aus dem föderierten
    # BPMN-Schritt der Absenderseite) -> lokaler `process_definition`-Name.
    # Rein konfigurationsbasiert, kein automatischer Prozesskatalog-Abgleich
    # zwischen Installationen (siehe docs/services/workflow-service.md).
    federation_process_type_map: dict[str, str] = {}

    # Lizenzvermittlung (Konzept 9.3, P9-S2): workflow-service ist die einzige
    # heute real existierende "Applikationskomponente" (9.1) und fragt ihren
    # eigenen Lizenzstatus beim registry-service ab (nicht direkt beim
    # license-service - Service-Isolation, die Registry bleibt einzige
    # Vermittlungsstelle). TTL-Cache-Client, Vorbild `permission_client.py`.
    license_status_cache_ttl_seconds: float = 15.0
