from typing import Literal

from dms_common import BaseServiceSettings
from pydantic import BaseModel, model_validator

SignatureLevel = Literal["ses", "aes", "qes"]


class SignatureProviderConfig(BaseModel):
    """Eine einzelne Signature-Provider-Connector-Instanz (3.10, Plugin-Prinzip
    wie die Storage-Backends, 3.3). `id` - nicht `type` - ist der eindeutige
    Schlüssel (gleiche Begründung wie `BackendTargetConfig`, ADR 0017): mehrere
    Connectoren desselben Typs sollen unabhängig konfigurierbar bleiben, sobald
    ein zweiter echter Anbieter (z. B. zwei QTSPs) hinzukommt.

    `type="qtsp"` ist im Schema bewusst vorgesehen, aber in dieser Session
    NICHT implementiert (kein akkreditierter externer Vertrauensdiensteanbieter
    verfügbar/testbar) - ein Konfigurationsversuch schlägt in der Factory
    (`connectors/__init__.py`) mit einer klaren Fehlermeldung fehl, siehe
    docs/services/signature-service.md "Offene Punkte"."""

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

    # Connector-Set (3.10) - Default-Seed: ein interner, selbstsignierter
    # Connector für SES/AES (siehe connectors/internal.py). Weitere Einträge
    # (insb. ein echter QTSP für QES) werden je Installation über diese
    # Env-Var ergänzt, ohne Code-Änderung - gleiches Muster wie `DMS_TARGETS`
    # bei storage-service.
    signature_providers: list[SignatureProviderConfig] = [
        SignatureProviderConfig(id="internal", type="internal", levels=["ses", "aes"])
    ]

    document_service_base_url: str = "http://localhost:8006"
    object_type_service_base_url: str = "http://localhost:8007"

    # Für die Existenzprüfung von `signer_principal_id` und das Auslesen von
    # Anzeigename/E-Mail fürs AES-Zertifikat (`GET /users`, seit P6-S5 gegated) -
    # gleiches Retrofit-Muster wie bei notification-service (P6-S6): Anmeldung
    # über das technische `users-admin`-Konto, kein Token-Caching.
    auth_service_base_url: str = "http://localhost:8003"
    auth_service_admin_username: str = "users-admin"
    auth_service_admin_password: str = "users-admin"
