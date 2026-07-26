import asyncio
import hashlib
import os
import uuid
from pathlib import Path

from storage_service.backends.interface import ObjectNotFoundError, StorageBackend


class LocalFilesystemBackend(StorageBackend):
    """Lokales Dateisystem als Backend (3.6).

    Deckt zugleich den NFS-Fall ab: In einer Kubernetes-Umgebung ist
    ``base_path`` der Mountpunkt eines PVC - ob darunter ein Block-Volume
    oder ein NFS-Export liegt, ist für diesen Code unsichtbar, da beide sich
    für Anwendungen als normaler Ordner verhalten. Ein separates NFS-Backend
    ist daher nicht nötig.

    **Schreibsicherheit statt Locking**: Statt plattformspezifischem
    File-Locking (fcntl), dessen Semantik über NFS-Implementierungen hinweg
    inkonsistent ist, wird atomar geschrieben (temporäre Datei + ``os.replace``)
    - das verhindert Teilschreib-Korruption bei gleichzeitigen Schreibern auf
    denselben Key, auch über NFSv4+. Nebenläufige *Bearbeitung* desselben
    Dokuments ist ohnehin Aufgabe des dokumentenweiten Lockings im Document
    Service (4.2), nicht dieser Abstraktionsschicht.
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

    async def write(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(self._write_sync, self._path_for(key), data)

    async def read(self, key: str) -> bytes:
        path = self._path_for(key)
        if not path.exists():
            raise ObjectNotFoundError(key)
        return await asyncio.to_thread(path.read_bytes)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path_for(key).unlink, True)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path_for(key).exists)

    async def checksum(self, key: str) -> str:
        data = await self.read(key)
        return hashlib.sha256(data).hexdigest()
