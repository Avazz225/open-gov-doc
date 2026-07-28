import json
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from storage_service import repository
from storage_service.backends.interface import ObjectNotFoundError, StorageBackend

logger = logging.getLogger(__name__)

# Reservierter Objekt-Key für die Identitätsdatei (3.6 "Datenträger-Wechsel-
# Sensibilität", P5b-S6) - bewusst über das bestehende StorageBackend-
# Interface (write/read) statt eines eigenen Backend-Methodenpaars, siehe
# ADR 0017. Kein Schrägstrich, damit der Key nicht mit einem echten,
# segmentierten Dokument-/OCR-/Rendition-Key kollidieren kann (die alle
# `typ/id/...`-Form haben) - dennoch ein dokumentierter Namensraum-Vorbehalt,
# kein technisch erzwungener.
IDENTITY_KEY = "__dms_storage_identity__"


async def _read_device_id(backend: StorageBackend) -> str | None:
    try:
        raw = await backend.read(IDENTITY_KEY)
    except ObjectNotFoundError:
        return None
    try:
        return json.loads(raw)["device_id"]
    except (ValueError, KeyError, TypeError):
        # Kaputte/fremde Marker-Datei - wie "fehlt" behandeln, nicht wie ein
        # Treffer interpretieren (sicherer als eine kaputte Datei zu vertrauen).
        return None


async def check_target_identity(
    session: AsyncSession, target_id: str, backend: StorageBackend
) -> bool:
    """Prüft die Geräte-ID eines Ziels gegen den zuletzt bekannten Wert in
    der Shared DB (3.6). Gibt `True` zurück, wenn das Ziel als verifiziert
    gilt - das schließt den Erststart eines Ziels ein (kein bekannter
    Referenzwert vorhanden, siehe Konsequenzen in ADR 0017: ein neu
    hinzugefügtes Ziel wird beim ersten Start automatisch "geprägt", nicht
    abgelehnt)."""
    known = await repository.get_backend_identity(session, target_id)

    try:
        found_device_id = await _read_device_id(backend)
        backend_reachable = True
    except Exception:
        logger.exception(
            "Identitätsprüfung für Ziel %r fehlgeschlagen (Backend nicht erreichbar?)", target_id
        )
        found_device_id = None
        backend_reachable = False

    if known is None:
        if found_device_id is not None:
            device_id = found_device_id
        elif backend_reachable:
            device_id = uuid.uuid4().hex
            await backend.write(IDENTITY_KEY, json.dumps({"device_id": device_id}).encode())
        else:
            return False
        await repository.record_backend_identity(session, target_id, device_id)
        return True

    if backend_reachable and found_device_id == known.device_id:
        await repository.record_backend_identity(session, target_id, known.device_id)
        return True

    logger.warning(
        "Datenträger-Identität für Ziel %r stimmt nicht überein oder ist nicht lesbar "
        "(erwartet %s, gefunden %s)",
        target_id,
        known.device_id,
        found_device_id,
    )
    return False
