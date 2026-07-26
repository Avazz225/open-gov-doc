from abc import ABC, abstractmethod


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
    async def write(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    async def read(self, key: str) -> bytes: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def exists(self, key: str) -> bool: ...

    @abstractmethod
    async def checksum(self, key: str) -> str:
        """SHA-256 über den tatsächlich im Backend gespeicherten Inhalt -
        Grundlage für Fixity-Checks (3.6): Vergleich gegen den in der
        Shared-DB hinterlegten Referenzwert."""
        ...
