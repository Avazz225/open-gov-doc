from typing import Literal

from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "storage-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Welches Backend aktiv ist (3.6). Mehrere gleichzeitige Ziele/Quorum-Schreiben
    # folgen erst in P3-S4 - hier zunächst genau ein konfiguriertes Backend.
    backend: Literal["local", "s3"] = "local"

    # Lokales Dateisystem-Backend: In einer Kubernetes-Umgebung ist das der
    # Mountpunkt eines PVC - ob darunter NFS oder ein Block-Volume liegt, ist
    # für dieses Backend irrelevant (siehe backends/local_backend.py).
    local_storage_base_path: str = "/tmp/dms-storage-dev"

    # S3-kompatibles Backend (Werkseinstellung MinIO für lokale Entwicklung).
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "dms_minio"
    s3_secret_key: str = "dms_minio_dev_only"
    s3_bucket: str = "dms-storage"
    s3_region: str = "us-east-1"
