from dataclasses import dataclass, field
from typing import Literal

# Wörtlich aus Konzept 3.3: "Capability-Beschreibung: lesen, schreiben,
# Metadaten, Locking, Versionierung - je nach Zielsystem unterschiedlich
# unterstützt".
ConnectorCapability = Literal["read", "write", "metadata", "locking", "versioning"]


@dataclass(frozen=True)
class ConnectorDescriptor:
    """Was ein einzelner Connector-Service tatsächlich unterstützt - dient
    sowohl als Selbstregistrierungs-Payload (`capabilities` bei
    `dms-registry-client`) als auch als dokumentierte Grundlage für ein
    künftiges Connector-Protokoll-Versions-Handshake (3.3). `version` nutzt
    absichtlich dasselbe Feld, das `registry-service`s `RegisterRequest`
    bereits für jeden Service kennt (`version: str`) - keine zweite,
    connector-spezifische Versionierung nötig."""

    protocol: str
    capabilities: frozenset[ConnectorCapability] = field(default_factory=frozenset)

    def as_capability_list(self) -> list[str]:
        return sorted(self.capabilities)
