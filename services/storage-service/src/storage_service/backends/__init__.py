from storage_service.backends.azure_backend import AzureBlobBackend
from storage_service.backends.interface import ObjectNotFoundError, StorageBackend
from storage_service.backends.local_backend import LocalFilesystemBackend
from storage_service.backends.s3_backend import S3Backend
from storage_service.settings import BackendTargetConfig, Settings


def build_backend(target: BackendTargetConfig) -> StorageBackend:
    """Builds a backend plugin (3.6) for a single, configured target
    instance - new backend types (e.g. Azure Blob) are added here without
    touching the rest of the service. Since P5b-S6, instantiated by
    `target.id` (not `target.type`) so that any number of instances of the
    same type (e.g. two S3 providers) can be configured independently
    (ADR 0017)."""
    if target.type == "local":
        assert target.base_path is not None  # already enforced by BackendTargetConfig
        return LocalFilesystemBackend(target.base_path)
    if target.type == "s3":
        assert target.endpoint_url and target.access_key and target.secret_key
        assert target.bucket and target.region
        return S3Backend(
            endpoint_url=target.endpoint_url,
            access_key=target.access_key,
            secret_key=target.secret_key,
            bucket=target.bucket,
            region=target.region,
            object_lock_enabled=target.object_lock_mode is not None,
        )
    if target.type == "azure":
        assert target.connection_string and target.container
        return AzureBlobBackend(
            connection_string=target.connection_string,
            container=target.container,
        )
    raise ValueError(f"Unbekannter Backend-Typ: {target.type!r}")


def resolve_targets(targets: list[BackendTargetConfig]) -> list[str]:
    """List of configured target `id`s, primary target first (this also
    determines read priority, see ``replication.read_with_fallback``).
    Since P7-S3 (5.6), excludes targets with ``role="archive"`` - these are
    not part of the regular upload replication, see
    ``resolve_archive_targets``. Since Post-Roadmap Phase 22 Session 7
    (ADR 0092) this function deliberately takes an already-resolved
    `BackendTargetConfig` list instead of reading `Settings` directly -
    depending on context, callers pass either the structural env-var list
    (`settings.targets`, e.g. when building the backend instances) or the
    list merged live with `TargetOverride` rows (`main.py._compute_target_state`,
    for `role`-dependent routing)."""
    return [target.id for target in targets if target.role != "archive"]


def resolve_archive_targets(targets: list[BackendTargetConfig]) -> list[str]:
    """List of configured archive target `id`s (5.6, since P7-S3) - these
    receive content exclusively via the `.../archive-copy` endpoints, not
    via the regular upload replication. Since Post-Roadmap Phase 22
    Session 7, also switched to a passed-in list, see `resolve_targets`."""
    return [target.id for target in targets if target.role == "archive"]


def build_backends(settings: Settings) -> dict[str, StorageBackend]:
    """Builds exactly the backend instances configured in the target set -
    any number, including several of the same type (3.6 "multiple
    devices", P5b-S6). Without redundancy, it stays at a single target, as
    before P3-S4."""
    return {target.id: build_backend(target) for target in settings.targets}


__all__ = [
    "ObjectNotFoundError",
    "AzureBlobBackend",
    "S3Backend",
    "LocalFilesystemBackend",
    "StorageBackend",
    "build_backend",
    "build_backends",
    "resolve_archive_targets",
    "resolve_targets",
]
