from typing import Literal

from dms_common import BaseServiceSettings
from pydantic import BaseModel, model_validator


class BackendTargetConfig(BaseModel):
    """Eine einzelne Backend-Instanz im Ziel-Set (3.6). Seit P5b-S6 (ADR 0017)
    ist `id` - nicht `type` - der eindeutige Schlüssel, daher sind beliebig
    viele gleichartige Instanzen möglich (z. B. zwei S3-Provider gleichzeitig
    im selben Ziel-Set), jede mit eigenen Zugangsdaten/Mount-Parametern."""

    id: str
    type: Literal["local", "s3"]
    base_path: str | None = None
    endpoint_url: str | None = None
    access_key: str | None = None
    secret_key: str | None = None
    bucket: str | None = None
    region: str | None = None

    @model_validator(mode="after")
    def _check_required_fields_for_type(self) -> "BackendTargetConfig":
        if self.type == "local" and not self.base_path:
            raise ValueError(f"Ziel {self.id!r}: base_path ist für type=local erforderlich")
        if self.type == "s3" and not all(
            [self.endpoint_url, self.access_key, self.secret_key, self.bucket, self.region]
        ):
            raise ValueError(
                f"Ziel {self.id!r}: endpoint_url/access_key/secret_key/bucket/region "
                "sind für type=s3 erforderlich"
            )
        return self


class Settings(BaseServiceSettings):
    service_name: str = "storage-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Ziel-Set (3.6) - seit P5b-S6 eine echte Liste von Backend-Instanzen statt
    # der vorherigen festen Zwei-Slot-Struktur (backend/secondary_backend,
    # siehe ADR 0004/0017). Primärziel ist immer der erste Eintrag (bestimmt
    # auch die Lesepriorität, siehe replication.read_with_fallback).
    targets: list[BackendTargetConfig] = [
        BackendTargetConfig(id="local", type="local", base_path="/tmp/dms-storage-dev")
    ]

    # Schreibstrategie (3.6): "quorum" = synchron, Erfolg erst ab quorum_count
    # bestätigten Zielen (Werkseinstellung für hohen Schutzbedarf laut Konzept);
    # "primary_async" = synchron nur aufs Primärziel, weitere Ziele werden als
    # "pending" vermerkt und über POST /replication/process-pending nachgezogen
    # (Werkseinstellung für den allgemeinen Arbeitsbetrieb).
    write_strategy: Literal["quorum", "primary_async"] = "primary_async"
    quorum_count: int = 1

    # Nach so vielen erfolglosen Replikationsversuchen gilt eine Kopie als
    # dauerhaft fehlgeschlagen ("failed_permanent") statt weiter für
    # process-pending vorgemerkt zu bleiben - Ersatz für die im Konzept
    # geforderte "Alarmierung", solange kein Notification Service existiert
    # (folgt Phase 6): wird stattdessen als Error-Log-Zeile ausgegeben.
    max_replication_attempts: int = 5
