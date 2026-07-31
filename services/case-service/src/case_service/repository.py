from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from case_service.models import Case, CaseDocumentReference


class NotFoundError(Exception):
    pass


class CaseClosedError(Exception):
    """Eine bereits abgeschlossene Umlaufmappe akzeptiert keine
    Referenzaenderungen mehr (2.3: Referenzstruktur ist ab Abschluss fixiert)."""


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
    Behandlung wie beim regulaeren Lesezugriff einer offenen Umlaufmappe."""
    case.status = "closed"
    case.closed_at = datetime.now(UTC)
    active = await get_active_references(session, case.id)
    for reference in active:
        if reference.document_id in snapshots:
            reference.snapshot_version_number = snapshots[reference.document_id]
    await session.flush()
    return case
