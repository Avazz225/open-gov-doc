import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from document_service.models import (
    AuditTraceConfig,
    AuditTraceRoleOverride,
    DeletionRegisterEntry,
    Document,
    DocumentLock,
    DocumentVersion,
    LegalHold,
    RetentionConfig,
    TrashConfig,
    UploadConfig,
)

_UPLOAD_CONFIG_ID = 1
_RETENTION_CONFIG_ID = 1
_TRASH_CONFIG_ID = 1
_AUDIT_TRACE_CONFIG_ID = 1


class NotFoundError(Exception):
    pass


class LockConflictError(Exception):
    """Ein anderer Nutzer hält aktuell die Bearbeitungssperre (4.2)."""


class LockNotHeldError(Exception):
    """Regulärer Unlock-Versuch durch jemanden, der die Sperre nicht hält."""


class NotDeletedError(Exception):
    """Wiederherstellungsversuch für ein Dokument, das gar nicht im
    Papierkorb liegt (5.2, seit P7-S1)."""


class RestorePeriodExpiredError(Exception):
    """Die konfigurierte Papierkorb-Wiederherstellungsfrist ist bereits
    abgelaufen (5.2, seit P7-S1)."""


class AlreadyReleasedError(Exception):
    """Ein Legal Hold wurde bereits zuvor aufgehoben (5.2, seit P7-S1)."""


async def get_document(session: AsyncSession, document_id: str) -> Document:
    document = await session.get(Document, document_id)
    if document is None:
        raise NotFoundError(f"document_id {document_id!r} unbekannt")
    return document


async def list_documents_by_folder(session: AsyncSession, folder_id: str) -> list[Document]:
    """Grundlage für die Ordner-Navigation der User-UI (P4-S2). `folder_id` wird
    hier wie überall in diesem Service als opake Fremdreferenz behandelt (keine
    Existenzprüfung gegen den Folder Service) - ein unbekannter Ordner liefert
    einfach eine leere Liste statt eines Fehlers."""
    result = await session.execute(
        select(Document)
        .where(Document.folder_id == folder_id, Document.deleted_at.is_(None))
        .order_by(Document.title)
    )
    return list(result.scalars().all())


async def create_document(
    session: AsyncSession,
    *,
    document_id: str,
    title: str,
    filename: str,
    content_type: str | None,
    size_bytes: int,
    checksum_sha256: str,
    storage_object_key: str,
    folder_id: str | None,
    object_type_id: int | None,
    attributes: dict,
    created_by: str,
    derived_from_document_id: str | None = None,
    derived_from_version_number: int | None = None,
    originating_case_id: str | None = None,
    retention_until: datetime | None = None,
    archive_after: datetime | None = None,
) -> Document:
    now = datetime.now(UTC)
    document = Document(
        id=document_id,
        title=title,
        folder_id=folder_id,
        object_type_id=object_type_id,
        attributes=attributes,
        current_version_number=1,
        created_by=created_by,
        created_at=now,
        updated_at=now,
        derived_from_document_id=derived_from_document_id,
        derived_from_version_number=derived_from_version_number,
        originating_case_id=originating_case_id,
        # Aufbewahrung (5.2, seit P7-S1): einmalig aus
        # `ObjectType.default_retention_days` übernommen, falls beim Anlegen
        # kein manuelles Datum gesetzt wurde (siehe main.py create_document) -
        # spätere Änderungen am Typ-Default wirken sich nicht rückwirkend aus.
        retention_until=retention_until,
        # Aussonderung (5.6, seit P7-S3): analog aus
        # `ObjectType.default_archive_after_days` übernommen, unabhängig von
        # retention_until (siehe models.py Document.archive_after).
        archive_after=archive_after,
    )
    session.add(document)
    session.add(
        DocumentVersion(
            document_id=document_id,
            version_number=1,
            storage_object_key=storage_object_key,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            checksum_sha256=checksum_sha256,
            is_conflict=False,
            based_on_version_number=None,
            created_by=created_by,
            created_at=now,
        )
    )
    await session.flush()
    return document


async def update_document_metadata(
    session: AsyncSession,
    document_id: str,
    *,
    title: str | None,
    attributes: dict | None,
) -> Document:
    """Metadaten-Update (P4-S4, Nutzer-Feedback: Attribute waren bisher nur bei
    der Erstellung setzbar). Ändert bewusst nicht `folder_id`/`object_type_id` -
    das wären Verschiebe- bzw. Retypisierungs-Operationen mit eigenen
    Konsistenzfragen, kein reines Metadaten-Update."""
    document = await get_document(session, document_id)
    if title is not None:
        document.title = title
    if attributes is not None:
        document.attributes = attributes
    document.updated_at = datetime.now(UTC)
    await session.flush()
    return document


async def delete_document(session: AsyncSession, document_id: str, *, deleted_by: str) -> Document:
    """Weiche Löschung (Sichtbarkeit aus, Metadaten bleiben) - manuell über
    die API ausgelöst. Seit P7-S1 wandert ein weich gelöschtes Dokument in
    den Papierkorb (`restore_document`/`list_deleted_documents`) und wird
    nach Ablauf von `TrashConfig.restore_period_days` automatisch physisch
    bereinigt (siehe `list_expired_trash`/main.py `_retention_poll_loop`)."""
    document = await get_document(session, document_id)
    document.deleted_at = datetime.now(UTC)
    document.updated_at = document.deleted_at
    await session.flush()
    return document


async def restore_document(session: AsyncSession, document_id: str) -> Document:
    """Papierkorb-Wiederherstellung (5.2, seit P7-S1) - nur innerhalb der
    konfigurierten Frist möglich (`TrashConfig.restore_period_days`)."""
    document = await get_document(session, document_id)
    if document.deleted_at is None:
        raise NotDeletedError(f"Dokument {document_id!r} ist nicht gelöscht")
    config = await get_trash_config(session)
    deadline = document.deleted_at + timedelta(days=config.restore_period_days)
    if datetime.now(UTC) > deadline:
        raise RestorePeriodExpiredError(
            f"Wiederherstellungsfrist ({config.restore_period_days} Tage) ist abgelaufen"
        )
    document.deleted_at = None
    document.updated_at = datetime.now(UTC)
    await session.flush()
    return document


async def cascade_trash_by_folder_ids(
    session: AsyncSession, folder_ids: list[str], *, via_folder_id: str, deleted_by: str
) -> list[str]:
    """Kaskadierter Papierkorb-Weg für einen ganzen Ordner-Teilbaum (5.2, seit
    P7-S1b) - vom `folder-service` synchron aufgerufen, wenn ein Ordner
    (inkl. Unterordnern) in den Papierkorb verschoben wird. `deleted_via_
    folder_id` markiert die Herkunft, damit `cascade_restore_by_via_folder_id`
    beim Wiederherstellen NUR diese Dokumente zurückholt, keine unabhängig
    einzeln gelöschten im selben Ordner."""
    if not folder_ids:
        return []
    result = await session.execute(
        select(Document).where(Document.folder_id.in_(folder_ids), Document.deleted_at.is_(None))
    )
    documents = list(result.scalars().all())
    now = datetime.now(UTC)
    for document in documents:
        document.deleted_at = now
        document.deleted_via_folder_id = via_folder_id
        document.updated_at = now
    await session.flush()
    return [document.id for document in documents]


async def cascade_restore_by_via_folder_id(session: AsyncSession, via_folder_id: str) -> list[str]:
    """Gegenstück zu `cascade_trash_by_folder_ids` - stellt nur Dokumente
    wieder her, die genau über diesen Ordner kaskadiert gelöscht wurden."""
    result = await session.execute(
        select(Document).where(Document.deleted_via_folder_id == via_folder_id)
    )
    documents = list(result.scalars().all())
    now = datetime.now(UTC)
    for document in documents:
        document.deleted_at = None
        document.deleted_via_folder_id = None
        document.updated_at = now
    await session.flush()
    return [document.id for document in documents]


async def count_active_by_folder_ids(session: AsyncSession, folder_ids: list[str]) -> int:
    """Nicht-leer-Prüfung vor Ordner-Zwangslöschung (5.2a, seit P7-S1b) -
    `folder-service` fragt darüber ab, ob noch aktive (nicht gelöschte)
    Dokumente im zu löschenden Teilbaum liegen."""
    if not folder_ids:
        return 0
    result = await session.execute(
        select(func.count())
        .select_from(Document)
        .where(Document.folder_id.in_(folder_ids), Document.deleted_at.is_(None))
    )
    return result.scalar_one()


async def list_deleted_documents(session: AsyncSession, folder_id: str) -> list[Document]:
    """Papierkorb-Inhalt eines Ordners (5.2, seit P7-S1) - Gegenstück zu
    `list_documents_by_folder` (dort werden gelöschte Dokumente ausgeschlossen)."""
    result = await session.execute(
        select(Document)
        .where(Document.folder_id == folder_id, Document.deleted_at.isnot(None))
        .order_by(Document.title)
    )
    return list(result.scalars().all())


async def hard_delete_document(session: AsyncSession, document_id: str) -> None:
    """Vollständige, unwiederbringliche Entfernung (5.2a, seit P7-S1) - im
    Unterschied zu `delete_document` (Soft-Delete) bleibt danach nichts mehr
    in diesem Schema übrig außer einem separaten `DeletionRegisterEntry`
    (siehe main.py._execute_forced_deletion/_purge_expired_trash), der
    bewusst KEINEN FK auf `Document.id` hat. Entfernt zuerst alle
    abhängigen Zeilen (Versionen, eine evtl. verwaiste Sperre, die
    Legal-Hold-Historie), damit die FK-Constraints nicht verletzt werden."""
    document = await get_document(session, document_id)
    for version in await list_versions(session, document_id):
        await session.delete(version)
    lock = await session.get(DocumentLock, document_id)
    if lock is not None:
        await session.delete(lock)
    for hold in await list_holds(session, document_id):
        await session.delete(hold)
    # Ohne expliziten Zwischen-Flush ordnet SQLAlchemys Unit-of-Work die
    # anschließende DELETE-Anweisung für `document` nicht zuverlässig NACH
    # den obigen (keine deklarierten `relationship()`s zwischen diesen
    # Modellen, nur rohe FK-Spalten) - Postgres lehnt die Löschung des
    # Elternobjekts sonst mit einer FK-Verletzung ab.
    await session.flush()
    await session.delete(document)
    await session.flush()


async def list_versions(session: AsyncSession, document_id: str) -> list[DocumentVersion]:
    await get_document(session, document_id)
    result = await session.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number)
    )
    return list(result.scalars().all())


async def get_version(
    session: AsyncSession, document_id: str, version_number: int
) -> DocumentVersion:
    result = await session.execute(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == version_number,
        )
    )
    version = result.scalar_one_or_none()
    if version is None:
        raise NotFoundError(f"Version {version_number} von {document_id!r} unbekannt")
    return version


async def get_current_version(session: AsyncSession, document_id: str) -> DocumentVersion:
    document = await get_document(session, document_id)
    return await get_version(session, document_id, document.current_version_number)


async def get_lock(session: AsyncSession, document_id: str) -> DocumentLock | None:
    return await session.get(DocumentLock, document_id)


def _is_active(lock: DocumentLock, now: datetime) -> bool:
    return lock.expires_at > now


async def acquire_lock(
    session: AsyncSession,
    document_id: str,
    *,
    locked_by: str,
    session_id: str,
    timeout_seconds: float,
) -> DocumentLock:
    document = await get_document(session, document_id)
    now = datetime.now(UTC)
    lock = await session.get(DocumentLock, document_id)

    if lock is not None and _is_active(lock, now) and lock.locked_by != locked_by:
        raise LockConflictError(f"Dokument {document_id!r} ist gesperrt von {lock.locked_by!r}")

    if lock is None:
        lock = DocumentLock(document_id=document_id)
        session.add(lock)

    lock.locked_by = locked_by
    lock.session_id = session_id
    lock.based_on_version_number = document.current_version_number
    lock.locked_at = now
    lock.expires_at = now + timedelta(seconds=timeout_seconds)
    await session.flush()
    return lock


async def release_lock(session: AsyncSession, document_id: str, *, released_by: str) -> None:
    lock = await session.get(DocumentLock, document_id)
    if lock is None:
        return  # bereits frei - idempotent, kein Fehler
    if lock.locked_by != released_by:
        raise LockNotHeldError(
            f"Sperre an {document_id!r} wird von {lock.locked_by!r} gehalten, "
            f"nicht von {released_by!r}"
        )
    await session.delete(lock)
    await session.flush()


async def force_release_lock(session: AsyncSession, document_id: str) -> DocumentLock:
    """Administrativer Force-Unlock (4.2). Gibt die zuvor aktive Sperre
    zurück, damit der Aufrufer den ursprünglichen Halter für Benachrichtigung/
    Audit kennt. Die eigentliche Konfliktkopie-Absicherung entsteht nicht hier,
    sondern optimistisch beim nächsten Check-in (siehe checkin_version) -
    siehe ADR 0002 für die Begründung dieser Vereinfachung."""
    lock = await session.get(DocumentLock, document_id)
    if lock is None:
        raise NotFoundError(f"Dokument {document_id!r} ist nicht gesperrt")
    await session.delete(lock)
    await session.flush()
    return lock


def _conflict_filename(filename: str, *, created_by: str, now: datetime) -> str:
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    stem, sep, ext = filename.rpartition(".")
    if sep:
        return f"{stem}_conflict_{created_by}_{timestamp}.{ext}"
    return f"{filename}_conflict_{created_by}_{timestamp}"


async def checkin_version(
    session: AsyncSession,
    document_id: str,
    *,
    expected_base_version_number: int,
    storage_object_key: str,
    filename: str,
    content_type: str | None,
    size_bytes: int,
    checksum_sha256: str,
    created_by: str,
    comment: str | None = None,
) -> tuple[DocumentVersion, bool]:
    """Optimistische Konflikterkennung (4.2, konkretisiert): stimmt die vom
    Client angegebene Ausgangsversion nicht mit der aktuellen Hauptversion
    überein (z. B. weil in der Zwischenzeit ein Force-Unlock + regulärer
    Check-in eines anderen Nutzers stattfand), wird der Upload NICHT
    überschreibend eingespielt, sondern als eigenständige, weiterhin
    abrufbare Konfliktkopie neben der aktuellen Version abgelegt - der
    Hauptversions-Zeiger bewegt sich in diesem Fall nicht.

    Gibt ``(version, is_conflict)`` zurück.
    """
    document = await get_document(session, document_id)
    lock = await session.get(DocumentLock, document_id)
    now = datetime.now(UTC)

    if lock is not None and _is_active(lock, now) and lock.locked_by != created_by:
        raise LockConflictError(f"Dokument {document_id!r} ist gesperrt von {lock.locked_by!r}")

    is_conflict = expected_base_version_number != document.current_version_number

    max_version = await session.execute(
        select(func.max(DocumentVersion.version_number)).where(
            DocumentVersion.document_id == document_id
        )
    )
    next_version_number = (max_version.scalar_one() or 0) + 1

    final_filename = (
        _conflict_filename(filename, created_by=created_by, now=now) if is_conflict else filename
    )

    version = DocumentVersion(
        document_id=document_id,
        version_number=next_version_number,
        storage_object_key=storage_object_key,
        filename=final_filename,
        content_type=content_type,
        size_bytes=size_bytes,
        checksum_sha256=checksum_sha256,
        is_conflict=is_conflict,
        based_on_version_number=expected_base_version_number,
        comment=comment,
        created_by=created_by,
        created_at=now,
    )
    session.add(version)

    if not is_conflict:
        document.current_version_number = next_version_number
        document.updated_at = now

    # Check-in beendet regulär die eigene Bearbeitung (4.2) - auch im
    # Konfliktfall, da die Ausgangsbasis ohnehin veraltet war und ein
    # erneuter Versuch ebenfalls über die Konflikterkennung liefe.
    if lock is not None and lock.locked_by == created_by:
        await session.delete(lock)

    await session.flush()
    return version, is_conflict


async def get_upload_config(session: AsyncSession) -> UploadConfig:
    """Liest die (einzige) Format-Whitelist-Zeile, legt sie mit Defaults an,
    falls sie noch nie gespeichert wurde - macht ein separates Migrations-/
    Seed-Skript überflüssig (gleiches Muster wie `ocr_service.get_config`)."""
    config = await session.get(UploadConfig, _UPLOAD_CONFIG_ID)
    if config is None:
        config = UploadConfig(
            id=_UPLOAD_CONFIG_ID, allowed_content_types=[], updated_at=datetime.now(UTC)
        )
        session.add(config)
        await session.flush()
    return config


async def update_upload_config(
    session: AsyncSession, *, allowed_content_types: list[str]
) -> UploadConfig:
    config = await get_upload_config(session)
    config.allowed_content_types = allowed_content_types
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


# --- Aufbewahrung/Legal Hold/Zwangslöschung (5.2/5.2a, seit P7-S1) ---------


async def set_retention(
    session: AsyncSession,
    document_id: str,
    *,
    retention_until: datetime | None,
    full_deletion: bool,
    reason: str | None,
    notify_email: str | None = None,
) -> Document:
    document = await get_document(session, document_id)
    document.retention_until = retention_until
    document.full_deletion = full_deletion
    document.pending_deletion_reason = reason
    document.reminder_notify_email = notify_email
    # Neu terminiert (oder Termin geändert) - eine bereits versendete
    # Erinnerung für den alten Termin ist hinfällig, eine neue kann wieder
    # fällig werden. Ein zuvor angelegter Freigabe-Request für den alten
    # Termin gilt ebenfalls nicht mehr automatisch.
    document.deletion_reminder_sent_at = None
    document.force_delete_approval_requested_at = None
    document.updated_at = datetime.now(UTC)
    await session.flush()
    return document


async def create_legal_hold(
    session: AsyncSession, document_id: str, *, set_by: str, reason: str | None
) -> LegalHold:
    await get_document(session, document_id)
    hold = LegalHold(
        id=str(uuid.uuid4()),
        document_id=document_id,
        reason=reason,
        set_by=set_by,
        set_at=datetime.now(UTC),
    )
    session.add(hold)
    await session.flush()
    return hold


async def release_legal_hold(session: AsyncSession, hold_id: str, *, released_by: str) -> LegalHold:
    hold = await session.get(LegalHold, hold_id)
    if hold is None:
        raise NotFoundError(f"Legal Hold {hold_id!r} unbekannt")
    if hold.released_at is not None:
        raise AlreadyReleasedError(f"Legal Hold {hold_id!r} wurde bereits aufgehoben")
    hold.released_by = released_by
    hold.released_at = datetime.now(UTC)
    await session.flush()
    return hold


async def list_holds(
    session: AsyncSession, document_id: str, *, active_only: bool = False
) -> list[LegalHold]:
    query = select(LegalHold).where(LegalHold.document_id == document_id)
    if active_only:
        query = query.where(LegalHold.released_at.is_(None))
    result = await session.execute(query.order_by(LegalHold.set_at.desc()))
    return list(result.scalars().all())


async def has_active_hold(session: AsyncSession, document_id: str) -> bool:
    result = await session.execute(
        select(LegalHold.id)
        .where(LegalHold.document_id == document_id, LegalHold.released_at.is_(None))
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def create_deletion_register_entry(
    session: AsyncSession,
    document_id: str,
    *,
    trigger: str,
    reason: str | None,
    triggered_by: str | None,
) -> DeletionRegisterEntry:
    entry = DeletionRegisterEntry(
        id=str(uuid.uuid4()),
        document_id=document_id,
        trigger=trigger,
        reason=reason,
        triggered_by=triggered_by,
        occurred_at=datetime.now(UTC),
    )
    session.add(entry)
    await session.flush()
    return entry


async def list_deletion_register(
    session: AsyncSession, *, document_id: str | None = None
) -> list[DeletionRegisterEntry]:
    query = select(DeletionRegisterEntry)
    if document_id is not None:
        query = query.where(DeletionRegisterEntry.document_id == document_id)
    result = await session.execute(query.order_by(DeletionRegisterEntry.occurred_at.desc()))
    return list(result.scalars().all())


async def get_retention_config(session: AsyncSession) -> RetentionConfig:
    config = await session.get(RetentionConfig, _RETENTION_CONFIG_ID)
    if config is None:
        config = RetentionConfig(
            id=_RETENTION_CONFIG_ID,
            deletion_reason_required=False,
            reminder_lead_days=None,
            updated_at=datetime.now(UTC),
        )
        session.add(config)
        await session.flush()
    return config


async def update_retention_config(
    session: AsyncSession, *, deletion_reason_required: bool, reminder_lead_days: int | None
) -> RetentionConfig:
    config = await get_retention_config(session)
    config.deletion_reason_required = deletion_reason_required
    config.reminder_lead_days = reminder_lead_days
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


async def get_trash_config(session: AsyncSession) -> TrashConfig:
    config = await session.get(TrashConfig, _TRASH_CONFIG_ID)
    if config is None:
        config = TrashConfig(
            id=_TRASH_CONFIG_ID, restore_period_days=30, updated_at=datetime.now(UTC)
        )
        session.add(config)
        await session.flush()
    return config


async def update_trash_config(session: AsyncSession, *, restore_period_days: int) -> TrashConfig:
    config = await get_trash_config(session)
    config.restore_period_days = restore_period_days
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


async def list_due_for_reminder(session: AsyncSession, *, lead_days: int) -> list[Document]:
    """Löscherinnerung (5.2a, optional): Dokumente, deren Frist innerhalb der
    Vorlaufzeit liegt, noch nicht gelöscht sind, noch keine Erinnerung
    bekamen und keinen aktiven Legal Hold haben."""
    threshold = datetime.now(UTC) + timedelta(days=lead_days)
    result = await session.execute(
        select(Document).where(
            Document.retention_until.isnot(None),
            Document.retention_until <= threshold,
            Document.deleted_at.is_(None),
            Document.deletion_reminder_sent_at.is_(None),
        )
    )
    candidates = list(result.scalars().all())
    return [d for d in candidates if not await has_active_hold(session, d.id)]


async def list_due_for_retention_action(session: AsyncSession) -> list[Document]:
    """Dokumente mit fälliger Aufbewahrungsfrist (5.2/5.2a) ohne aktiven
    Legal Hold - `full_deletion` entscheidet im Aufrufer (main.py), ob
    regulärer Soft-Delete oder physische Zwangslöschung folgt."""
    now = datetime.now(UTC)
    result = await session.execute(
        select(Document).where(
            Document.retention_until.isnot(None),
            Document.retention_until <= now,
            Document.deleted_at.is_(None),
        )
    )
    candidates = list(result.scalars().all())
    return [d for d in candidates if not await has_active_hold(session, d.id)]


async def list_expired_trash(session: AsyncSession, *, restore_period_days: int) -> list[Document]:
    """Papierkorb-Einträge, deren Wiederherstellungsfrist abgelaufen ist
    (5.2) - Legal Hold blockiert auch die routinemäßige Bereinigung."""
    deadline = datetime.now(UTC) - timedelta(days=restore_period_days)
    result = await session.execute(
        select(Document).where(Document.deleted_at.isnot(None), Document.deleted_at <= deadline)
    )
    candidates = list(result.scalars().all())
    return [d for d in candidates if not await has_active_hold(session, d.id)]


# --- Forensik-Trace: Audit-Tiefe (5.4b, seit P7-S2c) -----------------------


async def get_audit_trace_config(session: AsyncSession) -> AuditTraceConfig:
    """Liest die (einzige) Basis-Protokollierungstiefe-Zeile, legt sie mit
    Defaults an, falls sie noch nie gespeichert wurde (gleiches Muster wie
    `get_upload_config`) - Default laut Nutzervorgabe: beide Kategorien an."""
    config = await session.get(AuditTraceConfig, _AUDIT_TRACE_CONFIG_ID)
    if config is None:
        config = AuditTraceConfig(
            id=_AUDIT_TRACE_CONFIG_ID,
            log_viewed=True,
            log_downloaded=True,
            updated_at=datetime.now(UTC),
        )
        session.add(config)
        await session.flush()
    return config


async def update_audit_trace_config(
    session: AsyncSession, *, log_viewed: bool, log_downloaded: bool
) -> AuditTraceConfig:
    config = await get_audit_trace_config(session)
    config.log_viewed = log_viewed
    config.log_downloaded = log_downloaded
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


async def list_role_overrides(session: AsyncSession) -> list[AuditTraceRoleOverride]:
    result = await session.execute(
        select(AuditTraceRoleOverride).order_by(AuditTraceRoleOverride.role)
    )
    return list(result.scalars().all())


async def upsert_role_override(
    session: AsyncSession, role: str, *, log_viewed: bool | None, log_downloaded: bool | None
) -> AuditTraceRoleOverride:
    override = await session.get(AuditTraceRoleOverride, role)
    if override is None:
        override = AuditTraceRoleOverride(role=role, updated_at=datetime.now(UTC))
        session.add(override)
    override.log_viewed = log_viewed
    override.log_downloaded = log_downloaded
    override.updated_at = datetime.now(UTC)
    await session.flush()
    return override


async def delete_role_override(session: AsyncSession, role: str) -> None:
    override = await session.get(AuditTraceRoleOverride, role)
    if override is None:
        raise NotFoundError(f"Kein Rollen-Override für {role!r}")
    await session.delete(override)
    await session.flush()


def resolve_should_log(
    category: str,
    roles: set[str],
    config: AuditTraceConfig,
    overrides: list[AuditTraceRoleOverride],
) -> bool:
    """Löst auf, ob eine `document.viewed`/`document.downloaded`-Aktion für
    einen Aufrufer mit `roles` protokolliert werden soll. `category` ist
    `"viewed"` oder `"downloaded"`. Konfliktregel bei mehreren Rollen mit
    widersprüchlichen Overrides: protokollieren gewinnt (Sicherheits-first) -
    siehe Architekturentscheidung in PROGRESS.md/docs."""
    field = "log_viewed" if category == "viewed" else "log_downloaded"
    matching = [o for o in overrides if o.role in roles and getattr(o, field) is not None]
    if not matching:
        return bool(getattr(config, field))
    values = [getattr(o, field) for o in matching]
    if any(values):
        return True
    return False


# --- Aussonderung (5.6, seit P7-S3) ----------------------------------------


async def list_due_for_archival(session: AsyncSession) -> list[Document]:
    """Dokumente mit fälliger Aussonderung (5.6) - unabhängig von
    `retention_until`/`full_deletion` (5.2), da Aussonderung laut Konzept
    ergänzend zur regulären Aufbewahrungsfrist ist. `archival-service` ruft
    dies periodisch ab (interner Aufruf, `GET /documents/due-for-archival`)."""
    now = datetime.now(UTC)
    result = await session.execute(
        select(Document).where(
            Document.archive_after.isnot(None),
            Document.archive_after <= now,
            Document.archived_at.is_(None),
            Document.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def request_archive(session: AsyncSession, document_id: str) -> Document:
    """Manueller Aussonderungs-Trigger (5.6, `POST /documents/{id}/archive-
    request`) - setzt `archive_after` auf jetzt, falls noch nicht gesetzt
    oder noch nicht fällig. Ein bereits fälliges/vergangenes Datum bleibt
    unverändert (kein Zurückdrehen einer bereits laufenden Aussonderung)."""
    document = await get_document(session, document_id)
    now = datetime.now(UTC)
    if document.archive_after is None or document.archive_after > now:
        document.archive_after = now
        document.updated_at = now
        await session.flush()
    return document


async def mark_archived(
    session: AsyncSession, document_id: str, *, archive_format: str
) -> Document:
    """Rückruf von `archival-service`, sobald die Archivkopie verifiziert
    ist (`PUT /documents/{id}/archived`) - die `Document`-Zeile selbst
    bleibt vollständig erhalten (wörtliche Konzeptvorgabe, s. models.py)."""
    document = await get_document(session, document_id)
    document.archived_at = datetime.now(UTC)
    document.archive_format = archive_format
    document.updated_at = datetime.now(UTC)
    await session.flush()
    return document


async def mark_dehydrated(session: AsyncSession, document_id: str) -> Document:
    """Rückruf von `archival-service`, nachdem die Live-Speicherkopie nach
    Ablauf der Übergangsfrist entfernt wurde (`PUT /documents/{id}/
    dehydrated`)."""
    document = await get_document(session, document_id)
    document.dehydrated_at = datetime.now(UTC)
    document.updated_at = datetime.now(UTC)
    await session.flush()
    return document


async def mark_rehydrated(session: AsyncSession, document_id: str) -> Document:
    """Rückruf von `archival-service` nach erfolgreicher Rückholung (`PUT
    /documents/{id}/rehydrated`) - die Live-Kopie ist wiederhergestellt."""
    document = await get_document(session, document_id)
    document.dehydrated_at = None
    document.updated_at = datetime.now(UTC)
    await session.flush()
    return document
