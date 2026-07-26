from storage_service.backends.interface import ObjectNotFoundError, StorageBackend
from storage_service.backends.local_backend import LocalFilesystemBackend
from storage_service.backends.s3_backend import S3Backend
from storage_service.settings import Settings


def build_backend_by_type(settings: Settings, backend_type: str) -> StorageBackend:
    """Baut ein Backend-Plugin (3.6) für einen bestimmten Typ - neue Backends
    (z. B. Azure Blob) werden hier ergänzt, ohne den Rest des Service
    anzufassen. Getrennt von ``settings.backend``, damit Primär- und
    Sekundärziel unabhängig instanziiert werden können (Redundanz, P3-S4)."""
    if backend_type == "local":
        return LocalFilesystemBackend(settings.local_storage_base_path)
    if backend_type == "s3":
        return S3Backend(
            endpoint_url=settings.s3_endpoint_url,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            bucket=settings.s3_bucket,
            region=settings.s3_region,
        )
    raise ValueError(f"Unbekanntes Storage-Backend: {backend_type!r}")


def build_backend(settings: Settings) -> StorageBackend:
    """Baut nur das Primärbackend - Kompatibilitäts-Helfer für Aufrufer, die
    keine Redundanz brauchen (z. B. Tests)."""
    return build_backend_by_type(settings, settings.backend)


def resolve_targets(settings: Settings) -> list[str]:
    """Liste der konfigurierten Redundanz-Ziele, Primärziel zuerst (bestimmt
    auch die Lesepriorität, siehe ``replication.read_with_fallback``)."""
    targets = [settings.backend]
    if settings.redundancy_enabled:
        if settings.secondary_backend in ("none", settings.backend):
            raise ValueError(
                "secondary_backend muss gesetzt sein und sich von backend "
                "unterscheiden, wenn redundancy_enabled=true"
            )
        targets.append(settings.secondary_backend)
    return targets


def build_backends(settings: Settings) -> dict[str, StorageBackend]:
    """Baut genau die Backends, die als Ziel konfiguriert sind (nicht mehr) -
    ohne Redundanz bleibt es bei einem einzigen Backend, wie vor P3-S4."""
    return {target: build_backend_by_type(settings, target) for target in resolve_targets(settings)}


__all__ = [
    "ObjectNotFoundError",
    "S3Backend",
    "LocalFilesystemBackend",
    "StorageBackend",
    "build_backend",
    "build_backend_by_type",
    "build_backends",
    "resolve_targets",
]
