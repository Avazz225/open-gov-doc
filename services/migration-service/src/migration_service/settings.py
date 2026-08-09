from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "migration-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    document_service_base_url: str = "http://localhost:8006"
    folder_service_base_url: str = "http://localhost:8008"
    permission_service_base_url: str = "http://localhost:8004"
    workflow_service_base_url: str = "http://localhost:8014"

    # Vier-Augen-Prinzip (4.3, P6-S4-Muster): fragt vor dem Start eines Transfers
    # ab, ob "migration.transfer.start" gerade Genehmigung erfordert.
    approval_action_type: str = "migration.transfer.start"

    # Lizenzvermittlung (9.1/9.3, P9-S2-Muster) - Konzept 9.1 nennt
    # "Migration-Service" wörtlich als Beispiel für eine separat lizenzierbare
    # Komponente.
    license_status_cache_ttl_seconds: float = 30.0

    # Konfigurierbare Übergangs-/Aufbewahrungsfrist (7.2) - Default für neue
    # Transfers, falls beim Anlegen kein expliziter Wert mitgegeben wird.
    default_retention_days: int = 30

    # Timeout für Aufrufe gegen eine gepaarte Ziel-Installation (Kopieren
    # potenziell vieler Dokumente, siehe docs/services/migration-service.md).
    peer_call_timeout_seconds: float = 300.0
