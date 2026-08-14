from abc import ABC, abstractmethod
from datetime import datetime


class ObjectNotFoundError(Exception):
    pass


class StorageBackend(ABC):
    """Unified interface for storage backend plugins (concept 3.6): write,
    read, delete, existence check, checksum. New backends (Azure Blob,
    additional S3 providers, ...) only implement this interface - the rest
    of the storage service remains unchanged ("plug-in" principle).
    """

    @abstractmethod
    async def write(self, key: str, data: bytes, *, lock_until: datetime | None = None) -> None:
        """`lock_until` (5.1/5.2a, since P7-S1) is an optional, best-effort
        hint to the backend to keep the object technically immutable until
        this point in time (real S3 Object Lock in `S3Backend`) - the
        actual, portable enforcement is handled independently by
        `retention_guard.py` at the application level; a backend without a
        technical equivalent (e.g. `LocalFilesystemBackend`) simply ignores
        the parameter instead of raising an error."""
        ...

    @abstractmethod
    async def read(self, key: str) -> bytes: ...

    @abstractmethod
    async def delete(self, key: str, *, bypass_governance: bool = False) -> None:
        """`bypass_governance` (5.1/5.2a) is only evaluated by `S3Backend`
        when Object Lock is set (`BypassGovernanceRetention=True`) - the
        authorization check itself (role/approval) already happens before
        this call, in `retention_guard.py`/`main.py`, not here."""
        ...

    @abstractmethod
    async def exists(self, key: str) -> bool: ...

    @abstractmethod
    async def checksum(self, key: str) -> str:
        """SHA-256 over the content actually stored in the backend - the
        basis for fixity checks (3.6): comparison against the reference
        value stored in the shared DB."""
        ...
