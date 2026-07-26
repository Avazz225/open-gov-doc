from storage_service.backends.interface import ObjectNotFoundError, StorageBackend
from storage_service.backends.local_backend import LocalFilesystemBackend
from storage_service.backends.s3_backend import S3Backend
from storage_service.settings import Settings


def build_backend(settings: Settings) -> StorageBackend:
    """Wählt das konfigurierte Backend-Plugin (3.6) - neue Backends (z. B.
    Azure Blob) werden hier ergänzt, ohne den Rest des Service anzufassen."""
    if settings.backend == "local":
        return LocalFilesystemBackend(settings.local_storage_base_path)
    if settings.backend == "s3":
        return S3Backend(
            endpoint_url=settings.s3_endpoint_url,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            bucket=settings.s3_bucket,
            region=settings.s3_region,
        )
    raise ValueError(f"Unbekanntes Storage-Backend: {settings.backend!r}")


__all__ = [
    "ObjectNotFoundError",
    "S3Backend",
    "LocalFilesystemBackend",
    "StorageBackend",
    "build_backend",
]
