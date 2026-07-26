from dms_common import BaseServiceSettings

ROOT_FOLDER_ID = "root"


class Settings(BaseServiceSettings):
    service_name: str = "folder-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Objekttyp-Validierung ist optional - nur aufgerufen, wenn ein Folder
    # tatsächlich einen object_type_id trägt (2.2).
    object_type_service_base_url: str = "http://localhost:8007"
