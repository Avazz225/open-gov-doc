from abc import ABC, abstractmethod
from datetime import datetime


class ObjectNotFoundError(Exception):
    pass


class StorageBackend(ABC):
    """Einheitliches Interface für Storage-Backend-Plugins (Konzept 3.6):
    schreiben, lesen, löschen, Existenzprüfung, Prüfsumme. Neue Backends
    (Azure Blob, weitere S3-Provider, ...) implementieren nur dieses
    Interface - der Rest des Storage Service bleibt unverändert
    ("Dazustellen"-Prinzip).
    """

    @abstractmethod
    async def write(self, key: str, data: bytes, *, lock_until: datetime | None = None) -> None:
        """`lock_until` (5.1/5.2a, seit P7-S1) ist ein optionaler, best-effort
        Hinweis an das Backend, das Objekt bis zu diesem Zeitpunkt technisch
        unveränderlich zu halten (echtes S3 Object Lock beim `S3Backend`) -
        die eigentliche, portable Durchsetzung übernimmt unabhängig davon
        `retention_guard.py` auf Anwendungsebene, ein Backend ohne technische
        Entsprechung (z. B. `LocalFilesystemBackend`) ignoriert den Parameter
        einfach, statt einen Fehler zu werfen."""
        ...

    @abstractmethod
    async def read(self, key: str) -> bytes: ...

    @abstractmethod
    async def delete(self, key: str, *, bypass_governance: bool = False) -> None:
        """`bypass_governance` (5.1/5.2a) wird nur vom `S3Backend` bei
        gesetztem Object Lock ausgewertet (`BypassGovernanceRetention=True`) -
        die Autorisierungsprüfung selbst (Rolle/Freigabe) liegt bereits vor
        diesem Aufruf in `retention_guard.py`/`main.py`, nicht hier."""
        ...

    @abstractmethod
    async def exists(self, key: str) -> bool: ...

    @abstractmethod
    async def checksum(self, key: str) -> str:
        """SHA-256 über den tatsächlich im Backend gespeicherten Inhalt -
        Grundlage für Fixity-Checks (3.6): Vergleich gegen den in der
        Shared-DB hinterlegten Referenzwert."""
        ...
