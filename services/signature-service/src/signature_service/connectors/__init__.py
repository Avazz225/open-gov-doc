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
    """Baut einen Connector (3.10) für eine einzelne konfigurierte Instanz -
    neue Connector-Typen (insbesondere ein echter QTSP für QES) werden hier
    ergänzt, ohne den Rest des Service anzufassen (gleiches Muster wie
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
    """Erstes konfiguriertes Connector, das das angeforderte Niveau
    unterstützt (Reihenfolge der übergebenen `providers`-Liste) - `None`,
    wenn kein Connector dieses Niveau anbietet (z. B. `level="qes"` ohne
    konfigurierten QTSP). Seit Post-Roadmap Phase 22 Session 6 (ADR 0091)
    nimmt dieser Parameter bewusst die bereits mit `SignatureConfig`s
    live-editierbaren `levels` gemergte Liste entgegen (`main.py`), nicht
    mehr `Settings` direkt - `id`/`type` bleiben strukturell aus `Settings`,
    nur `levels` kann inzwischen vom Env-Var-Ausgangswert abweichen."""
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
