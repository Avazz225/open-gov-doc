from typing import Literal

from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "storage-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Primäres Backend (3.6) - immer aktiv, auch ohne Redundanz.
    backend: Literal["local", "s3"] = "local"

    # Storage-Redundanz (3.6): Ein zweites Ziel neben dem Primärbackend.
    # Bewusst auf genau zwei Ziele (die beiden vorhandenen Backend-Typen)
    # begrenzt statt einer generischen Ziel-Liste - siehe ADR 0004.
    redundancy_enabled: bool = False
    secondary_backend: Literal["local", "s3", "none"] = "none"

    # Schreibstrategie (3.6): "quorum" = synchron, Erfolg erst ab quorum_count
    # bestätigten Zielen (Werkseinstellung für hohen Schutzbedarf laut Konzept);
    # "primary_async" = synchron nur aufs Primärziel, Sekundärziele werden als
    # "pending" vermerkt und über POST /replication/process-pending nachgezogen
    # (Werkseinstellung für den allgemeinen Arbeitsbetrieb).
    write_strategy: Literal["quorum", "primary_async"] = "primary_async"
    quorum_count: int = 2

    # Nach so vielen erfolglosen Replikationsversuchen gilt eine Kopie als
    # dauerhaft fehlgeschlagen ("failed_permanent") statt weiter für
    # process-pending vorgemerkt zu bleiben - Ersatz für die im Konzept
    # geforderte "Alarmierung", solange kein Notification Service existiert
    # (folgt Phase 6): wird stattdessen als Error-Log-Zeile ausgegeben.
    max_replication_attempts: int = 5

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
