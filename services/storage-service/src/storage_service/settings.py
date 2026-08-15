from typing import Literal

from dms_common import BaseServiceSettings
from pydantic import BaseModel, model_validator


class BackendTargetConfig(BaseModel):
    """A single backend instance in the target set (3.6). Since P5b-S6
    (ADR 0017), `id` - not `type` - is the unique key, so any number of
    instances of the same type is possible (e.g. two S3 providers
    simultaneously in the same target set), each with its own
    credentials/mount parameters."""

    id: str
    type: Literal["local", "s3", "azure"]
    base_path: str | None = None
    endpoint_url: str | None = None
    access_key: str | None = None
    secret_key: str | None = None
    bucket: str | None = None
    region: str | None = None
    # Azure Blob Storage (3.6, concept 1a) - connection-string auth (no
    # azure-identity/AAD), see AzureBlobBackend/docs/services/
    # storage-service.md. `container` is the Azure counterpart to `bucket`
    # (its own field instead of reusing `bucket`, so both types remain
    # unambiguously distinguishable in error messages/configuration).
    connection_string: str | None = None
    container: str | None = None
    # WORM/Object Lock (5.1/5.2a, since P7-S1) - deliberately only
    # "governance" as a valid value (no "compliance"): compliance mode
    # would make the forced-deletion exception required by the concept
    # technically impossible. Only effective for type="s3" as real S3
    # Object Lock (see S3Backend.ensure_bucket/write) - the application-
    # layer guard (retention_guard.py) applies independent of the backend
    # type. For type="azure" as well as type="local", there is no
    # technical equivalent (documented no-op, see AzureBlobBackend) - it
    # is merely accepted so that an Azure target can still participate in
    # the application-layer guard.
    object_lock_mode: Literal["governance"] | None = None
    # Records disposal (5.6, since P7-S3): "archive" targets are NOT part
    # of the regular upload replication (`resolve_targets` excludes
    # them) - they receive content exclusively via the new
    # `.../archive-copy` endpoints, which `archival-service` calls
    # explicitly. `None` (default) = normal replication target,
    # unchanged existing behavior.
    role: Literal["archive"] | None = None

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
        if self.type == "azure" and not all([self.connection_string, self.container]):
            raise ValueError(
                f"Ziel {self.id!r}: connection_string/container sind für type=azure erforderlich"
            )
        return self


class Settings(BaseServiceSettings):
    service_name: str = "storage-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    monitoring_service_base_url: str = "http://localhost:8026"

    # Target set (3.6) - since P5b-S6 a real list of backend instances
    # instead of the previous fixed two-slot structure (backend/
    # secondary_backend, see ADR 0004/0017). The primary target is always
    # the first entry (this also determines read priority, see
    # replication.read_with_fallback).
    targets: list[BackendTargetConfig] = [
        BackendTargetConfig(id="local", type="local", base_path="/tmp/dms-storage-dev")
    ]

    # Write strategy (3.6): "quorum" = synchronous, success only once
    # quorum_count targets have confirmed (default for high protection
    # needs per the concept); "primary_async" = synchronous only to the
    # primary target, additional targets are marked "pending" and caught
    # up via POST /replication/process-pending (default for general
    # day-to-day operation).
    write_strategy: Literal["quorum", "primary_async"] = "primary_async"
    quorum_count: int = 1

    # After this many unsuccessful replication attempts, a copy is
    # considered permanently failed ("failed_permanent") instead of
    # remaining queued for process-pending - a stand-in for the
    # "alerting" required by the concept, as long as no Notification
    # Service exists (planned for Phase 6): an error-log line is emitted
    # instead.
    max_replication_attempts: int = 5

    # Role that may force a deletion despite an active governance lock
    # (`?bypass_governance=true`, 5.1/5.2a) - checked via the
    # `X-DMS-Roles` header forwarded by the gateway, the same server-side
    # role-header pattern as `document_service.kennzeichen_admin_role`.
    governance_bypass_role: str = "dms-admin"
