from signature_service.connectors.interface import (
    SignatureProviderConnector,
    SignedResult,
    SignerInfo,
    VerificationResult,
)
from signature_service.connectors.internal import InternalSelfSignedConnector, generate_root_ca
from signature_service.settings import Settings, SignatureProviderConfig


def build_connector(
    config: SignatureProviderConfig, *, ca_certificate_pem: bytes, ca_private_key_pem: bytes
) -> SignatureProviderConnector:
    """Builds a connector (3.10) for a single configured instance - new
    connector types (especially a real QTSP for QES) are added here without
    touching the rest of the service (same pattern as
    `storage_service.backends.build_backend`)."""
    if config.type == "internal":
        return InternalSelfSignedConnector(ca_certificate_pem, ca_private_key_pem)
    if config.type == "qtsp":
        raise ValueError(
            f"Connector {config.id!r}: type=qtsp ist im Schema vorgesehen, aber in dieser "
            "Session nicht implementiert - kein akkreditierter externer Vertrauensdiensteanbieter "
            "verfügbar (siehe docs/services/signature-service.md 'Offene Punkte')"
        )
    raise ValueError(f"Unbekannter Connector-Typ: {config.type!r}")


def build_connectors(
    settings: Settings, *, ca_certificate_pem: bytes, ca_private_key_pem: bytes
) -> dict[str, SignatureProviderConnector]:
    return {
        config.id: build_connector(
            config, ca_certificate_pem=ca_certificate_pem, ca_private_key_pem=ca_private_key_pem
        )
        for config in settings.signature_providers
    }


def resolve_connector_for_level(
    providers: list[SignatureProviderConfig],
    connectors: dict[str, SignatureProviderConnector],
    level: str,
) -> tuple[str, SignatureProviderConnector] | None:
    """First configured connector that supports the requested level (order
    of the passed-in `providers` list) - `None` if no connector offers this
    level (e.g. `level="qes"` without a configured QTSP). Since post-roadmap
    phase 22 session 6 (ADR 0091), this parameter deliberately receives the
    list already merged with `SignatureConfig`'s live-editable `levels`
    (`main.py`), no longer `Settings` directly - `id`/`type` still come
    structurally from `Settings`, only `levels` can now differ from the
    env-var starting value."""
    for config in providers:
        if level in config.levels:
            return config.id, connectors[config.id]
    return None


__all__ = [
    "InternalSelfSignedConnector",
    "SignatureProviderConnector",
    "SignedResult",
    "SignerInfo",
    "VerificationResult",
    "build_connector",
    "build_connectors",
    "generate_root_ca",
    "resolve_connector_for_level",
]
