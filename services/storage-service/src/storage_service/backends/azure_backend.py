import hashlib
from datetime import datetime

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob.aio import BlobServiceClient

from storage_service.backends.interface import ObjectNotFoundError, StorageBackend


class AzureBlobBackend(StorageBackend):
    """Azure Blob Storage backend (3.6, concept 1a) - connection-string auth
    (no `azure-identity`/AAD, see `docs/services/storage-service.md`),
    works identically against real Azure Blob Storage and the local
    Azurite emulator (default setting for tests/dev, analogous to MinIO for
    `S3Backend`).

    **`lock_until` is deliberately a documented no-op here**, not real
    Azure Immutable Blob Storage (versioning-/policy-based time-based
    retention): Azurite - this project's reference test environment - does
    not yet support immutability policies, and a "technical WORM" untested
    against Azurite would be a pretended rather than a real protection.
    Same stance as with `LocalFilesystemBackend` (see ADR 0030): the
    portable, actual enforcement is handled independently of the backend
    type by `retention_guard.py` at the application level anyway -
    `S3Backend`'s real Object Lock is additional hardening, not a
    prerequisite.
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
                pass  # already exists - idempotent, analogous to S3Backend.ensure_bucket

    async def write(self, key: str, data: bytes, *, lock_until: datetime | None = None) -> None:
        # `lock_until` is deliberately ignored, not simulated - see the
        # class docstring.
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
        # `bypass_governance` has no effect here (no real Object Lock
        # equivalent, see the class docstring) - still accepted to satisfy
        # the same `StorageBackend` interface.
        async with self._service_client() as service:
            blob_client = service.get_blob_client(self._container, key)
            try:
                await blob_client.delete_blob()
            except ResourceNotFoundError:
                pass  # already gone - idempotent as elsewhere

    async def exists(self, key: str) -> bool:
        async with self._service_client() as service:
            blob_client = service.get_blob_client(self._container, key)
            return await blob_client.exists()

    async def checksum(self, key: str) -> str:
        data = await self.read(key)
        return hashlib.sha256(data).hexdigest()
