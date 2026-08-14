from dataclasses import dataclass, field
from typing import Literal

# Verbatim from concept 3.3: "capability description: read, write,
# metadata, locking, versioning - support varies by target system".
ConnectorCapability = Literal["read", "write", "metadata", "locking", "versioning"]


@dataclass(frozen=True)
class ConnectorDescriptor:
    """What a single connector service actually supports - serves both as
    the self-registration payload (`capabilities` for `dms-registry-client`)
    and as a documented basis for a future connector protocol version
    handshake (3.3). `version` deliberately reuses the same field that
    `registry-service`'s `RegisterRequest` already has for every service
    (`version: str`) - no second, connector-specific versioning needed."""

    protocol: str
    capabilities: frozenset[ConnectorCapability] = field(default_factory=frozenset)

    def as_capability_list(self) -> list[str]:
        return sorted(self.capabilities)
