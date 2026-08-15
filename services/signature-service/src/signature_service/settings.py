from typing import Literal

from dms_common import BaseServiceSettings
from pydantic import BaseModel, model_validator

SignatureLevel = Literal["ses", "aes", "qes"]


class SignatureProviderConfig(BaseModel):
    """A single signature provider connector instance (3.10, plugin
    principle like the storage backends, 3.3). `id` - not `type` - is the
    unique key (same rationale as `BackendTargetConfig`, ADR 0017): multiple
    connectors of the same type should remain independently configurable
    once a second real provider (e.g. two QTSPs) is added.

    `type="qtsp"` is deliberately provided for in the schema but NOT
    implemented in this session (no accredited external trust service
    provider available/testable) - a configuration attempt fails in the
    factory (`connectors/__init__.py`) with a clear error message, see
    docs/services/signature-service.md "Open Points"."""

    id: str
    type: Literal["internal", "qtsp"]
    levels: list[SignatureLevel]

    @model_validator(mode="after")
    def _check_levels(self) -> "SignatureProviderConfig":
        if not self.levels:
            raise ValueError(f"Connector {self.id!r}: levels darf nicht leer sein")
        if self.type == "internal" and "qes" in self.levels:
            raise ValueError(
                f"Connector {self.id!r}: type=internal kann kein QES ausstellen "
                "(erfordert einen akkreditierten externen QTSP)"
            )
        return self


class Settings(BaseServiceSettings):
    service_name: str = "signature-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Connector set (3.10) - default seed: an internal, self-signed
    # connector for SES/AES (see connectors/internal.py). Further entries
    # (especially a real QTSP for QES) are added per installation via this
    # env var, without a code change - same pattern as `DMS_TARGETS` on
    # storage-service.
    signature_providers: list[SignatureProviderConfig] = [
        SignatureProviderConfig(id="internal", type="internal", levels=["ses", "aes"])
    ]

    document_service_base_url: str = "http://localhost:8006"
    object_type_service_base_url: str = "http://localhost:8007"

    # For checking the existence of `signer_principal_id` and reading the
    # display name/email for the AES certificate (`GET /users`, gated since
    # P6-S5) - same retrofit pattern as with notification-service (P6-S6):
    # logs in via the technical `users-admin` account, no token caching.
    auth_service_base_url: str = "http://localhost:8003"
    auth_service_admin_username: str = "users-admin"
    auth_service_admin_password: str = "users-admin"

    monitoring_service_base_url: str = "http://localhost:8026"
