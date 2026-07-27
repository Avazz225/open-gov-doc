from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ScanVerdict:
    clean: bool
    threat_name: str | None = None


class ScanEngine(ABC):
    """Einheitliches Interface für Virenscan-Engine-Plugins (10.3, ADR 0010),
    nach demselben "Dazustellen"-Prinzip wie die Storage-Backends (3.3/3.6):
    neue Engines implementieren nur dieses Interface, der Rest des Service
    bleibt unverändert."""

    @abstractmethod
    async def scan(self, data: bytes) -> ScanVerdict: ...
