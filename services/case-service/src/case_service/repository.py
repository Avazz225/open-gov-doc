import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from case_service.models import (
    Case,
    CaseArchivalConfig,
    CaseDocumentReference,
    CaseNumberConfig,
    CaseSequence,
)

_ARCHIVAL_CONFIG_ID = 1
_NUMBER_CONFIG_ID = 1
_VORGANGSNUMMER_PLACEHOLDERS = {"YYYY", "YY", "Laufende_Nummer"}
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_]+)\}")


class NotFoundError(Exception):
    pass


class InvalidFieldError(Exception):
    pass


class CaseClosedError(Exception):
    """An already-closed circulation folder no longer accepts reference
    changes (2.3: the reference structure is fixed from closure onward)."""


class CaseNotClosedError(Exception):
    """Records disposal (5.6, since P7-S3b) is only possible for already-
    closed circulation folders."""


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
    vorgangsnummer: str | None = None,
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
        vorgangsnummer=vorgangsnummer,
    )
    session.add(case)
    await session.flush()
    return case


async def list_cases_by_vorgangsnummer(session: AsyncSession, vorgangsnummer: str) -> list[Case]:
    """For the new `mail-connector` (2.5/3.3, P15-S3), which wants to match
    incoming mail to a circulation folder based on a case number found in
    the subject/body. Returns a list instead of a single object, consistent
    with `document_service.repository.list_documents_by_kennzeichen` - even
    though the case number is globally unique by construction, this keeps
    the caller robust against a future format change."""
    result = await session.execute(select(Case).where(Case.vorgangsnummer == vorgangsnummer))
    return list(result.scalars().all())


def _render_vorgangsnummer(format_str: str, *, jahr: int, laufende_nummer: int) -> str:
    return format_str.format(
        YYYY=f"{jahr:04d}", YY=f"{jahr % 100:02d}", Laufende_Nummer=f"{laufende_nummer:03d}"
    )


async def get_case_number_config(session: AsyncSession) -> CaseNumberConfig:
    config = await session.get(CaseNumberConfig, _NUMBER_CONFIG_ID)
    if config is None:
        config = CaseNumberConfig(id=_NUMBER_CONFIG_ID, updated_at=datetime.now(UTC))
        session.add(config)
        await session.flush()
    return config


def _validate_vorgangsnummer_format(format_str: str) -> None:
    used = set(_PLACEHOLDER_RE.findall(format_str))
    unknown = sorted(used - _VORGANGSNUMMER_PLACEHOLDERS)
    if unknown:
        raise InvalidFieldError(f"format enthält unbekannte Platzhalter: {unknown}")
    if "Laufende_Nummer" not in used:
        raise InvalidFieldError("format muss den Platzhalter {Laufende_Nummer} enthalten")


async def update_case_number_format(session: AsyncSession, *, format: str) -> CaseNumberConfig:
    _validate_vorgangsnummer_format(format)
    config = await get_case_number_config(session)
    config.format = format
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


async def _next_case_sequence_number(session: AsyncSession, jahr: int) -> int:
    """Atomic yearly counter (P15-S3) - identical idiom to
    object_type_service.repository._next_sequence_number (P5e-S1), here
    without an object-type dimension."""
    insert_stmt = (
        pg_insert(CaseSequence)
        .values(jahr=jahr, naechste_nummer=1)
        .on_conflict_do_nothing(index_elements=["jahr"])
    )
    await session.execute(insert_stmt)

    result = await session.execute(
        select(CaseSequence).where(CaseSequence.jahr == jahr).with_for_update()
    )
    row = result.scalar_one()
    assigned = row.naechste_nummer
    row.naechste_nummer = assigned + 1
    await session.flush()
    return assigned


async def next_vorgangsnummer(session: AsyncSession) -> str:
    config = await get_case_number_config(session)
    jahr = datetime.now(UTC).year
    laufende_nummer = await _next_case_sequence_number(session, jahr)
    return _render_vorgangsnummer(config.format, jahr=jahr, laufende_nummer=laufende_nummer)


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
    """Closure snapshot (2.3): fixes, for every active reference, the
    version current at the time of closure - later changes to the
    referenced original document no longer affect this circulation folder
    afterward. `document_id`s missing from `snapshots` (e.g. because the
    document was no longer reachable at closure) are left without a
    `snapshot_version_number` - the same "remains traceably present"
    handling as for regular read access to an open circulation folder.

    Also resolves `archive_after` at the same time (5.6, since P7-S3b) -
    unlike `Document.archive_after` (resolved on creation), only here,
    since only closed circulation folders are eligible for disposal."""
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
    """Closed circulation folders with due records disposal (5.6, since
    P7-S3b) - `archival-service` polls this periodically (`GET /cases/due-
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
    """Manual records disposal trigger (5.6, `POST /cases/{id}/archive-
    request`) - only for already-closed circulation folders, sets
    `archive_after` to now if not yet set or not yet due."""
    case = await get_case(session, case_id)
    if case.status != "closed":
        raise CaseNotClosedError(f"Umlaufmappe {case_id!r} ist noch nicht abgeschlossen")
    now = datetime.now(UTC)
    if case.archive_after is None or case.archive_after > now:
        case.archive_after = now
        await session.flush()
    return case


async def mark_archived(session: AsyncSession, case_id: str) -> Case:
    """Callback from `archival-service` once the XDOMEA package is verified
    (`PUT /cases/{id}/archived`) - the `Case` row itself remains fully
    intact (verbatim concept requirement, see models.py)."""
    case = await get_case(session, case_id)
    case.archived_at = datetime.now(UTC)
    await session.flush()
    return case
