from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ScanVerdict:
    clean: bool
    threat_name: str | None = None


class ScanEngine(ABC):
    """Uniform interface for virus scan engine plugins (10.3, ADR 0010),
    following the same "plug in alongside" principle as the storage backends
    (3.3/3.6): new engines only need to implement this interface, the rest
    of the service stays unchanged."""

    @abstractmethod
    async def scan(self, data: bytes) -> ScanVerdict: ...
