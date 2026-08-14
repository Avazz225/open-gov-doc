import base64
from abc import ABC, abstractmethod


class KeyNotFoundError(Exception):
    pass


class KeyStore(ABC):
    """Plugin interface for archive encryption keys (5.6, ADR 0029) - the
    same pattern as `storage_service.backends.interface.StorageBackend`
    (ADR 0017). The core service only ships this interface plus a trivial
    default implementation (`EnvKeyStore`); a real KDBX integration
    (`pykeepass`, GPL-3.0) is, per ADR 0029, deliberately a separately
    installable plugin outside the standard image."""

    @abstractmethod
    def get_key(self, key_id: str) -> bytes:
        """Returns the 32-byte AES-256 key for `key_id`."""


class EnvKeyStore(KeyStore):
    """A single key from `Settings.archive_encryption_key` (base64-encoded)
    - explicitly intended only for development/testing, not production key
    management (no rotation/multi-tenant support). `key_id` is ignored
    since there is only this one key."""

    def __init__(self, encoded_key: str | None) -> None:
        self._key = base64.b64decode(encoded_key) if encoded_key else None

    def get_key(self, key_id: str) -> bytes:
        # Deliberately no fallback to a randomly generated key: it would
        # change on every restart and permanently render already-encrypted
        # archive copies undecryptable.
        if self._key is None:
            raise KeyNotFoundError(
                "Kein Archiv-Verschlüsselungsschlüssel konfiguriert "
                "(DMS_ARCHIVE_ENCRYPTION_KEY) - erforderlich für Objekttypen mit "
                "archive_encryption_enabled=true"
            )
        return self._key
