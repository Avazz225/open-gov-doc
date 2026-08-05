import base64
from abc import ABC, abstractmethod


class KeyNotFoundError(Exception):
    pass


class KeyStore(ABC):
    """Plugin-Schnittstelle fuer Archiv-Verschluesselungsschluessel (5.6,
    ADR 0029) - gleiches Muster wie `storage_service.backends.interface.
    StorageBackend` (ADR 0017). Der Kernservice liefert nur diese
    Schnittstelle plus eine triviale Standardimplementierung
    (`EnvKeyStore`); eine echte KDBX-Anbindung (`pykeepass`, GPL-3.0) ist
    laut ADR 0029 bewusst ein separat nachinstallierbares Plugin außerhalb
    des Standard-Images."""

    @abstractmethod
    def get_key(self, key_id: str) -> bytes:
        """Gibt den 32-Byte-AES-256-Schluessel fuer `key_id` zurueck."""


class EnvKeyStore(KeyStore):
    """Ein einzelner Schluessel aus `Settings.archive_encryption_key`
    (base64-kodiert) - ausdruecklich nur fuer Entwicklung/Tests gedacht,
    kein Produktions-Schluesselmanagement (kein Rotation-/Multi-Tenant-
    Support). `key_id` wird ignoriert, da es nur diesen einen Schluessel
    gibt."""

    def __init__(self, encoded_key: str | None) -> None:
        self._key = base64.b64decode(encoded_key) if encoded_key else None

    def get_key(self, key_id: str) -> bytes:
        # Bewusst kein Fallback auf einen zufällig erzeugten Schlüssel: der
        # würde bei jedem Neustart wechseln und bereits verschlüsselte
        # Archiv-Kopien dauerhaft unentschlüsselbar machen.
        if self._key is None:
            raise KeyNotFoundError(
                "Kein Archiv-Verschlüsselungsschlüssel konfiguriert "
                "(DMS_ARCHIVE_ENCRYPTION_KEY) - erforderlich für Objekttypen mit "
                "archive_encryption_enabled=true"
            )
        return self._key
