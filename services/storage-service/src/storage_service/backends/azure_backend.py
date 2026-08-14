import hashlib
from datetime import datetime

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob.aio import BlobServiceClient

from storage_service.backends.interface import ObjectNotFoundError, StorageBackend


class AzureBlobBackend(StorageBackend):
    """Azure Blob Storage Backend (3.6, Konzept 1a) - Verbindungsstring-Auth
    (kein `azure-identity`/AAD, siehe `docs/services/storage-service.md`),
    funktioniert identisch gegen echtes Azure Blob Storage und den lokalen
    Azurite-Emulator (Werkseinstellung für Tests/Dev, analog zu MinIO bei
    `S3Backend`).

    **`lock_until` ist hier bewusst ein dokumentierter No-Op**, kein echtes
    Azure Immutable Blob Storage (versionierungs-/richtlinienbasiertes
    Time-Based Retention): Azurite - die Referenz-Testumgebung dieses
    Projekts - unterstützt Immutability-Policies bislang nicht, ein gegen
    Azurite ungetestetes "technisches WORM" wäre ein vorgetäuschter statt
    ein echter Schutz. Gleiche Haltung wie beim `LocalFilesystemBackend`
    (siehe ADR 0030): die portable, tatsächliche Durchsetzung übernimmt
    unabhängig vom Backend-Typ ohnehin `retention_guard.py` auf
    Anwendungsebene - `S3Backend`s echtes Object Lock ist eine zusätzliche
    Härtung, keine Voraussetzung.
    """

    def __init__(self, *, connection_string: str, container: str) -> None:
        self._connection_string = connection_string
        self._container = container

    def _service_client(self) -> BlobServiceClient:
        return BlobServiceClient.from_connection_string(self._connection_string)

    async def ensure_container(self) -> None:
        async with self._service_client() as service:
            container_client = service.get_container_client(self._container)
            try:
                await container_client.create_container()
            except ResourceExistsError:
                pass  # bereits vorhanden - idempotent, analog zu S3Backend.ensure_bucket

    async def write(self, key: str, data: bytes, *, lock_until: datetime | None = None) -> None:
        # `lock_until` wird bewusst ignoriert, nicht simuliert - siehe
        # Klassen-Docstring.
        async with self._service_client() as service:
            blob_client = service.get_blob_client(self._container, key)
            await blob_client.upload_blob(data, overwrite=True)

    async def read(self, key: str) -> bytes:
        async with self._service_client() as service:
            blob_client = service.get_blob_client(self._container, key)
            try:
                downloader = await blob_client.download_blob()
                return await downloader.readall()
            except ResourceNotFoundError as exc:
                raise ObjectNotFoundError(key) from exc

    async def delete(self, key: str, *, bypass_governance: bool = False) -> None:
        # `bypass_governance` ist hier ohne Wirkung (kein echtes Object-Lock-
        # Äquivalent, siehe Klassen-Docstring) - wird trotzdem entgegen-
        # genommen, um dasselbe `StorageBackend`-Interface zu erfüllen.
        async with self._service_client() as service:
            blob_client = service.get_blob_client(self._container, key)
            try:
                await blob_client.delete_blob()
            except ResourceNotFoundError:
                pass  # bereits nicht (mehr) vorhanden - idempotent wie sonst auch

    async def exists(self, key: str) -> bool:
        async with self._service_client() as service:
            blob_client = service.get_blob_client(self._container, key)
            return await blob_client.exists()

    async def checksum(self, key: str) -> str:
        data = await self.read(key)
        return hashlib.sha256(data).hexdigest()
