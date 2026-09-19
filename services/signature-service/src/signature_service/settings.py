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
    # display name/email for the AES certificate, via the service-to-service
    # `GET /users/service-directory` (capability `service.user_lookup`,
    # `X-DMS-Principal: signature-service`, see `AuthServiceClient`). Since
    # Phase 50 Session 2: no longer a `users-admin` login (that was a real
    # excess-privilege exposure - see `docs/services/signature-service.md`
    # "Open Points"), so no credential settings needed any more either.
    auth_service_base_url: str = "http://localhost:8003"

    monitoring_service_base_url: str = "http://localhost:8026"

    # RBAC (Post-Roadmap Phase 38 Session 3) - `PUT /signature-config`
    # previously had NO permission check at all.
    permission_service_base_url: str = "http://localhost:8004"

    # PAdES-B-LTA (3.10, Post-Roadmap Phase 41 Session 1): how often the
    # background poll loop checks for signatures whose archive-timestamp
    # chain is due for extension, and how old a signature's last
    # extension (or, for a never-extended one, its original signing) must
    # be before it's picked up. Daily/yearly are deliberately generous
    # defaults - the leaf certificate's own 5-year validity
    # (`connectors/internal.py`) is the actual deadline this is racing
    # against, so there is no need for a tight cadence.
    retimestamp_poll_interval_seconds: int = 24 * 60 * 60
    retimestamp_interval_days: int = 365
