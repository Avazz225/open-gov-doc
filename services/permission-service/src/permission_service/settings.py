from dms_common import BaseServiceSettings

ROOT_RESOURCE_ID = "root"


class Settings(BaseServiceSettings):
    service_name: str = "permission-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Provisorischer Vertrag (siehe docs/services/permission-service.md): welche
    # Subjects Struktur-Events (Ressource angelegt/verschoben/gelöscht) liefern.
    # Erwarteter Producer: Folder Service (P3-S3, noch nicht gebaut) unter
    # Stream "folder" - bis dahin per Tests simuliert.
    structure_subjects: list[str] = ["folder.>"]
