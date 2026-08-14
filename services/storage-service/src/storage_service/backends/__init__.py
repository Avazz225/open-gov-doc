from storage_service.backends.interface import ObjectNotFoundError, StorageBackend
from storage_service.backends.local_backend import LocalFilesystemBackend
from storage_service.backends.s3_backend import S3Backend
from storage_service.settings import BackendTargetConfig, Settings


def build_backend(target: BackendTargetConfig) -> StorageBackend:
    """Baut ein Backend-Plugin (3.6) für eine einzelne, konfigurierte
    Ziel-Instanz - neue Backend-Typen (z. B. Azure Blob) werden hier ergänzt,
    ohne den Rest des Service anzufassen. Seit P5b-S6 nach `target.id`
    (nicht `target.type`) instanziiert, damit beliebig viele gleichartige
    Instanzen (z. B. zwei S3-Provider) unabhängig konfiguriert werden können
    (ADR 0017)."""
    if target.type == "local":
        assert target.base_path is not None  # von BackendTargetConfig bereits erzwungen
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
    raise ValueError(f"Unbekannter Backend-Typ: {target.type!r}")


def resolve_targets(targets: list[BackendTargetConfig]) -> list[str]:
    """Liste der konfigurierten Ziel-`id`s, Primärziel zuerst (bestimmt auch
    die Lesepriorität, siehe ``replication.read_with_fallback``). Schließt
    seit P7-S3 (5.6) Ziele mit ``role="archive"`` aus - diese sind nicht
    Teil der regulären Upload-Replikation, siehe ``resolve_archive_targets``.
    Seit Post-Roadmap Phase 22 Session 7 (ADR 0092) nimmt diese Funktion
    bewusst eine bereits aufgelöste `BackendTargetConfig`-Liste entgegen statt
    `Settings` direkt zu lesen - Aufrufer übergeben je nach Kontext entweder
    die strukturelle Env-Var-Liste (`settings.targets`, z. B. beim Bau der
    Backend-Instanzen) oder die live mit `TargetOverride`-Zeilen gemergte
    Liste (`main.py._compute_target_state`, für `role`-abhängiges Routing)."""
    return [target.id for target in targets if target.role != "archive"]


def resolve_archive_targets(targets: list[BackendTargetConfig]) -> list[str]:
    """Liste der konfigurierten Archiv-Ziel-`id`s (5.6, seit P7-S3) - erhalten
    Inhalte ausschließlich über die `.../archive-copy`-Endpunkte, nicht über
    die reguläre Upload-Replikation. Seit Post-Roadmap Phase 22 Session 7
    ebenfalls auf eine übergebene Liste umgestellt, siehe `resolve_targets`."""
    return [target.id for target in targets if target.role == "archive"]


def build_backends(settings: Settings) -> dict[str, StorageBackend]:
    """Baut genau die Backend-Instanzen, die im Ziel-Set konfiguriert sind -
    beliebig viele, auch mehrere desselben Typs (3.6 "Mehrfach-Devices",
    P5b-S6). Ohne Redundanz bleibt es bei einem einzigen Ziel, wie vor
    P3-S4."""
    return {target.id: build_backend(target) for target in settings.targets}


__all__ = [
    "ObjectNotFoundError",
    "S3Backend",
    "LocalFilesystemBackend",
    "StorageBackend",
    "build_backend",
    "build_backends",
    "resolve_archive_targets",
    "resolve_targets",
]
