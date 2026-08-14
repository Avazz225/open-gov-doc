import json
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from storage_service import repository
from storage_service.backends.interface import ObjectNotFoundError, StorageBackend
from storage_service.models import BackendIdentity

logger = logging.getLogger(__name__)

# Reserved object key for the identity file (3.6 "storage device swap
# sensitivity", P5b-S6) - deliberately via the existing StorageBackend
# interface (write/read) instead of a dedicated backend method pair, see
# ADR 0017. No slash, so the key cannot collide with a real, segmented
# document/OCR/rendition key (all of which have `type/id/...` form) - still
# a documented namespace reservation, not a technically enforced one.
IDENTITY_KEY = "__dms_storage_identity__"


async def _read_device_id(backend: StorageBackend) -> str | None:
    try:
        raw = await backend.read(IDENTITY_KEY)
    except ObjectNotFoundError:
        return None
    try:
        return json.loads(raw)["device_id"]
    except (ValueError, KeyError, TypeError):
        # Corrupt/foreign marker file - treat as "missing", not as a
        # match (safer than trusting a corrupt file).
        return None


async def check_target_identity(
    session: AsyncSession, target_id: str, backend: StorageBackend
) -> bool:
    """Checks a target's device ID against the last known value in the
    shared DB (3.6). Returns `True` if the target counts as verified -
    this includes a target's first start (no known reference value
    present, see consequences in ADR 0017: a newly added target is
    automatically "stamped" on first start, not rejected)."""
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
        # Rebalancing (3.6/7.2, P5c-S2): a target newly added to the target
        # set has no copies of already-existing objects yet - this (first-
        # start bootstrap) is the only place where "new" can be reliably
        # detected without introducing a second change-detection
        # mechanism. On a completely fresh install, `object_metadata` is
        # empty, so the call is a cheap no-op.
        seeded = await repository.seed_pending_copies_for_new_target(session, target_id)
        if seeded:
            logger.info(
                "Rebalancing: %s bestehende Objekt(e) für neues Ziel %r zur "
                "Nachreplikation vorgemerkt",
                seeded,
                target_id,
            )
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


async def reidentify_target(
    session: AsyncSession, target_id: str, backend: StorageBackend
) -> BackendIdentity:
    """Correction mechanism for an intended, legitimate storage device
    swap (3.6, ADR 0017 "consequences", P5c-S2) - replaces the previously
    necessary direct correction in `backend_identity` with an API call at
    runtime, without a restart. Adopts an already-present marker file of
    the new device (e.g. a target that was already stamped elsewhere in
    the same system), otherwise a new one is written - as in the first-
    start bootstrap in `check_target_identity`. Since the physical device
    has changed, all previous copies on this target are considered lost
    and are reset to `pending`, just as with a degraded start
    (`repository.reset_copies_for_backend`) - the same retry queue
    (`POST /replication/process-pending`) picks them up. Deliberately
    lets errors during backend access propagate (no silent "treat as
    missing" as in `_read_device_id`, since an unreachable backend here
    must be a hard failure of the action, not a valid starting state)."""
    found_device_id = await _read_device_id(backend)
    device_id = found_device_id
    if device_id is None:
        device_id = uuid.uuid4().hex
        await backend.write(IDENTITY_KEY, json.dumps({"device_id": device_id}).encode())

    identity = await repository.record_backend_identity(session, target_id, device_id)
    reset_count = await repository.reset_copies_for_backend(session, target_id)
    logger.warning(
        "Datenträger-Wechsel für Ziel %r akzeptiert (neue Geräte-ID %s) - %s Kopie(n) auf "
        "'pending' zurückgesetzt",
        target_id,
        device_id,
        reset_count,
    )
    return identity
