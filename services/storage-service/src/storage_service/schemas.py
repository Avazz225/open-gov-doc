from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ObjectMetadataOut(BaseModel):
    object_key: str
    backend: str
    checksum_sha256: str
    size_bytes: int
    content_type: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class VerifyResult(BaseModel):
    ok: bool
    expected: str
    actual: str


class ObjectCopyOut(BaseModel):
    object_key: str
    backend_id: str
    status: str
    checksum_sha256: str | None
    attempts: int
    last_error: str | None
    next_retry_at: datetime | None
    retention_until: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FixityEntry(BaseModel):
    backend_id: str
    status: str
    ok: bool | None
    expected: str | None = None
    actual: str | None = None


class ReplicationRunResult(BaseModel):
    processed: int
    succeeded: int
    failed: int
    permanently_failed: int


class GuardConfigIn(BaseModel):
    allow_degraded_start: bool = False


class GuardConfigOut(GuardConfigIn):
    model_config = {"from_attributes": True}

    updated_at: datetime


class OperationalConfigIn(BaseModel):
    write_strategy: Literal["quorum", "primary_async"]
    quorum_count: int
    max_replication_attempts: int


class OperationalConfigOut(OperationalConfigIn):
    model_config = {"from_attributes": True}

    updated_at: datetime


class StorageUsageEntry(BaseModel):
    """Speicherverbrauch je Backend (5.4a, seit P7-S2b) - Grundlage für den
    entsprechenden Standardbericht im Reporting Service."""

    backend: str
    object_count: int
    total_size_bytes: int


class GuardStatusEntry(BaseModel):
    target_id: str
    device_id: str | None
    verified_at: datetime | None
    pending_copies: int
    # Aufbewahrung/WORM (5.1/5.2a, seit P7-S1). Seit Post-Roadmap Phase 22
    # Session 7 (ADR 0092) live editierbar über `PUT /guard-status/{id}/config`
    # (`TargetOverride`) - das Ziel-Set selbst (Zugangsdaten/Struktur) bleibt
    # weiterhin reine Deployment-Konfiguration.
    object_lock_mode: str | None = None
    role: str | None = None


class TargetConfigIn(BaseModel):
    """Post-Roadmap Phase 22 Session 7 (ADR 0092) - dieselben Literal-Werte
    wie `BackendTargetConfig.object_lock_mode`/`.role` (`settings.py`)."""

    object_lock_mode: Literal["governance"] | None = None
    role: Literal["archive"] | None = None
