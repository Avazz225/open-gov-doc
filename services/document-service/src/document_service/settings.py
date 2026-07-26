from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "document-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Document Service hält keine Dateiinhalte selbst, sondern spricht dafür
    # ausschließlich die HTTP-API des Storage Service an (3.6) - kein Import
    # von dessen Interna, reine Service-zu-Service-Kommunikation.
    storage_service_base_url: str = "http://localhost:8005"

    # Timeout ohne Aktivität, nach dem eine Bearbeitungssperre automatisch als
    # abgelaufen gilt (4.2) - kein Hintergrund-Sweep nötig, siehe repository.py.
    default_lock_timeout_seconds: float = 1800.0
