import asyncio
import hashlib
import os
import uuid
from datetime import datetime
from pathlib import Path

from storage_service.backends.interface import ObjectNotFoundError, StorageBackend


class LocalFilesystemBackend(StorageBackend):
    """Local filesystem as a backend (3.6).

    This also covers the NFS case: in a Kubernetes environment,
    ``base_path`` is the mount point of a PVC - whether a block volume or
    an NFS export sits underneath is invisible to this code, since both
    behave as an ordinary folder from the application's perspective. A
    separate NFS backend is therefore not needed.

    **Write safety instead of locking**: instead of platform-specific file
    locking (fcntl), whose semantics are inconsistent across NFS
    implementations, writes are atomic (temporary file + ``os.replace``) -
    this prevents partial-write corruption from concurrent writers on the
    same key, even over NFSv4+. Concurrent *editing* of the same document
    is handled by the document-wide locking in the Document Service (4.2)
    regardless, not by this abstraction layer.
    """

    def __init__(self, base_path: str) -> None:
        self._base_path = Path(base_path)
        self._base_path.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        resolved_base = self._base_path.resolve()
        path = (self._base_path / key).resolve()
        if path != resolved_base and resolved_base not in path.parents:
            raise ValueError(f"Ungültiger Objekt-Key (Path Traversal?): {key!r}")
        return path

    def _write_sync(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.parent / f".{path.name}.tmp-{uuid.uuid4().hex}"
        tmp_path.write_bytes(data)
        os.replace(tmp_path, path)

    async def write(self, key: str, data: bytes, *, lock_until: datetime | None = None) -> None:
        # `lock_until` has no technical equivalent on the local filesystem
        # (no Object Lock equivalent) - deliberately ignored, not
        # simulated. Portable enforcement is handled by
        # `retention_guard.py` independent of the backend type.
        await asyncio.to_thread(self._write_sync, self._path_for(key), data)

    async def read(self, key: str) -> bytes:
        path = self._path_for(key)
        if not path.exists():
            raise ObjectNotFoundError(key)
        return await asyncio.to_thread(path.read_bytes)

    async def delete(self, key: str, *, bypass_governance: bool = False) -> None:
        await asyncio.to_thread(self._path_for(key).unlink, True)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path_for(key).exists)

    async def checksum(self, key: str) -> str:
        data = await self.read(key)
        return hashlib.sha256(data).hexdigest()
