from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from case_service.models import Case, CaseArchivalConfig, CaseDocumentReference

_ARCHIVAL_CONFIG_ID = 1


class NotFoundError(Exception):
    pass


class CaseClosedError(Exception):
    """Eine bereits abgeschlossene Umlaufmappe akzeptiert keine
    Referenzaenderungen mehr (2.3: Referenzstruktur ist ab Abschluss fixiert)."""


class CaseNotClosedError(Exception):
    """Aussonderung (5.6, seit P7-S3b) ist nur fuer bereits abgeschlossene
    Umlaufmappen moeglich."""


async def create_case(
    session: AsyncSession,
    *,
    case_id: str,
    name: str,
    object_type_id: int | None,
    attributes: dict,
    process_definition_id: int,
    process_instance_id: str | None,
    created_by: str,
) -> Case:
    case = Case(
        id=case_id,
        name=name,
        object_type_id=object_type_id,
        attributes=attributes,
        status="open",
        process_definition_id=process_definition_id,
        process_instance_id=process_instance_id,
        created_by=created_by,
        created_at=datetime.now(UTC),
    )
    session.add(case)
    await session.flush()
    return case


async def get_case(session: AsyncSession, case_id: str) -> Case:
    case = await session.get(Case, case_id)
    if case is None:
        raise NotFoundError(f"case_id {case_id!r} unbekannt")
    return case


async def get_case_or_none(session: AsyncSession, case_id: str) -> Case | None:
    return await session.get(Case, case_id)


async def list_cases(
    session: AsyncSession, *, status: str | None = None, object_type_id: int | None = None
) -> list[Case]:
    stmt = select(Case)
    if status is not None:
        stmt = stmt.where(Case.status == status)
    if object_type_id is not None:
        stmt = stmt.where(Case.object_type_id == object_type_id)
    result = await session.execute(stmt.order_by(Case.created_at))
    return list(result.scalars().all())


async def add_document_reference(
    session: AsyncSession, case_id: str, *, document_id: str, added_by: str
) -> CaseDocumentReference:
    case = await get_case(session, case_id)
    if case.status != "open":
        raise CaseClosedError(f"Umlaufmappe {case_id!r} ist bereits abgeschlossen")
    reference = CaseDocumentReference(
        case_id=case_id,
        document_id=document_id,
        added_by=added_by,
        added_at=datetime.now(UTC),
    )
    session.add(reference)
    await session.flush()
    return reference


async def remove_document_reference(
    session: AsyncSession, case_id: str, document_id: str, *, removed_by: str
) -> CaseDocumentReference:
    case = await get_case(session, case_id)
    if case.status != "open":
        raise CaseClosedError(f"Umlaufmappe {case_id!r} ist bereits abgeschlossen")
    result = await session.execute(
        select(CaseDocumentReference).where(
            CaseDocumentReference.case_id == case_id,
            CaseDocumentReference.document_id == document_id,
            CaseDocumentReference.removed_at.is_(None),
        )
    )
    reference = result.scalars().first()
    if reference is None:
        raise NotFoundError(f"Aktive Referenz auf {document_id!r} in {case_id!r} unbekannt")
    reference.removed_by = removed_by
    reference.removed_at = datetime.now(UTC)
    await session.flush()
    return reference


async def list_document_references(
    session: AsyncSession, case_id: str
) -> list[CaseDocumentReference]:
    await get_case(session, case_id)
    result = await session.execute(
        select(CaseDocumentReference)
        .where(CaseDocumentReference.case_id == case_id)
        .order_by(CaseDocumentReference.added_at)
    )
    return list(result.scalars().all())


async def get_active_references(session: AsyncSession, case_id: str) -> list[CaseDocumentReference]:
    result = await session.execute(
        select(CaseDocumentReference).where(
            CaseDocumentReference.case_id == case_id,
            CaseDocumentReference.removed_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def close_case(session: AsyncSession, case: Case, *, snapshots: dict[str, int]) -> Case:
    """Abschluss-Snapshot (2.3): fixiert fuer jede aktive Referenz die zum
    Abschlusszeitpunkt aktuelle Version - spaetere Aenderungen am
    referenzierten Originaldokument wirken sich danach nicht mehr auf diese
    Umlaufmappe aus. `snapshots` fehlende `document_id`s (z. B. weil das
    Dokument beim Abschluss nicht mehr erreichbar war) bleiben ohne
    `snapshot_version_number` - dieselbe "bleibt nachvollziehbar bestehen"-
    Behandlung wie beim regulaeren Lesezugriff einer offenen Umlaufmappe.

    Loest zugleich `archive_after` auf (5.6, seit P7-S3b) - anders als bei
    `Document.archive_after` (bei Anlage aufgeloest) erst hier, da nur
    geschlossene Umlaufmappen aussonderungsfaehig sind."""
    case.status = "closed"
    case.closed_at = datetime.now(UTC)
    active = await get_active_references(session, case.id)
    for reference in active:
        if reference.document_id in snapshots:
            reference.snapshot_version_number = snapshots[reference.document_id]

    config = await get_archival_config(session)
    if config.default_archive_after_days_closed is not None:
        case.archive_after = case.closed_at + timedelta(
            days=config.default_archive_after_days_closed
        )

    await session.flush()
    return case


async def get_archival_config(session: AsyncSession) -> CaseArchivalConfig:
    config = await session.get(CaseArchivalConfig, _ARCHIVAL_CONFIG_ID)
    if config is None:
        config = CaseArchivalConfig(
            id=_ARCHIVAL_CONFIG_ID,
            default_archive_after_days_closed=None,
            archive_encryption_enabled=False,
            updated_at=datetime.now(UTC),
        )
        session.add(config)
        await session.flush()
    return config


async def update_archival_config(
    session: AsyncSession,
    *,
    default_archive_after_days_closed: int | None,
    archive_encryption_enabled: bool,
) -> CaseArchivalConfig:
    config = await get_archival_config(session)
    config.default_archive_after_days_closed = default_archive_after_days_closed
    config.archive_encryption_enabled = archive_encryption_enabled
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


async def list_due_for_archival(session: AsyncSession) -> list[Case]:
    """Geschlossene Umlaufmappen mit faelliger Aussonderung (5.6, seit
    P7-S3b) - `archival-service` ruft dies periodisch ab (`GET /cases/due-
    for-archival`)."""
    now = datetime.now(UTC)
    result = await session.execute(
        select(Case).where(
            Case.status == "closed",
            Case.archive_after.isnot(None),
            Case.archive_after <= now,
            Case.archived_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def request_archive(session: AsyncSession, case_id: str) -> Case:
    """Manueller Aussonderungs-Trigger (5.6, `POST /cases/{id}/archive-
    request`) - nur fuer bereits geschlossene Umlaufmappen, setzt
    `archive_after` auf jetzt, falls noch nicht gesetzt oder noch nicht
    faellig."""
    case = await get_case(session, case_id)
    if case.status != "closed":
        raise CaseNotClosedError(f"Umlaufmappe {case_id!r} ist noch nicht abgeschlossen")
    now = datetime.now(UTC)
    if case.archive_after is None or case.archive_after > now:
        case.archive_after = now
        await session.flush()
    return case


async def mark_archived(session: AsyncSession, case_id: str) -> Case:
    """Rueckruf von `archival-service`, sobald das XDOMEA-Paket verifiziert
    ist (`PUT /cases/{id}/archived`) - die `Case`-Zeile selbst bleibt
    vollstaendig erhalten (wörtliche Konzeptvorgabe, s. models.py)."""
    case = await get_case(session, case_id)
    case.archived_at = datetime.now(UTC)
    await session.flush()
    return case
