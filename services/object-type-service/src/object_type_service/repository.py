import re
from datetime import UTC, datetime

from dms_constraint_engine import ROOT_PARENT_TYPE
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from object_type_service.models import (
    KennzeichenConfig,
    ObjectType,
    ObjectTypeLayout,
    ObjectTypeSequence,
)
from object_type_service.schemas import LayoutIn, ObjectTypeCreate, ObjectTypeUpdate

# Kennzeichengenerator-Platzhalter (P5e-S1) - siehe PROGRESS.md "Kennzeichengenerator".
KENNZEICHEN_PLACEHOLDERS = {"YYYY", "YY", "MM", "DD", "Laufende_Nummer"}
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_]+)\}")
_KENNZEICHEN_CONFIG_ID = 1


class NotFoundError(Exception):
    pass


class DuplicateNameError(Exception):
    pass


class NoKennzeichenFormatError(Exception):
    """Kein `kennzeichen_format` für diesen Objekttyp konfiguriert - vom
    Aufrufer als 404 zu behandeln (P5e-S1)."""


class InvalidFieldError(Exception):
    """Fachlicher Validierungsfehler an einem der neuen 2.2a-Felder
    (``allowed_parent_types``/``icon``) - vom Aufrufer als 422 zu behandeln,
    anders als der 404/409 der übrigen Repository-Fehler."""


async def _validate_allowed_parent_types(
    session: AsyncSession, allowed_parent_types: list[str] | None
) -> None:
    """``allowedParentTypes`` (2.2a) darf nur auf ``"$ROOT"`` oder bereits
    existierende Ordnerklassen (``applies_to == "folder"``) verweisen - nur
    Ordner können Elternobjekte sein. Keine rückwirkende Prüfung bestehender
    Ablagen, falls eine referenzierte Klasse später gelöscht wird (siehe
    Konzept 13, offener Punkt) - eine dann "hängende" Referenz führt lediglich
    dazu, dass der betroffene Elterntyp beim nächsten Platzierungs-Check nicht
    mehr aufgelöst werden kann und wie ein unbekannter Typ behandelt wird."""
    if not allowed_parent_types:
        return
    names_to_check = {name for name in allowed_parent_types if name != ROOT_PARENT_TYPE}
    if not names_to_check:
        return
    result = await session.execute(
        select(ObjectType.name, ObjectType.applies_to).where(ObjectType.name.in_(names_to_check))
    )
    found = dict(result.all())
    missing = names_to_check - found.keys()
    if missing:
        raise InvalidFieldError(
            f"allowedParentTypes referenziert unbekannte Objekttypen: {sorted(missing)}"
        )
    not_folder = sorted(name for name, applies_to in found.items() if applies_to != "folder")
    if not_folder:
        raise InvalidFieldError(
            "allowedParentTypes darf nur auf Ordnerklassen (applies_to='folder') verweisen, "
            f"nicht auf: {not_folder}"
        )


def _validate_icon(applies_to: str, icon: str | None) -> None:
    if icon is not None and applies_to != "folder":
        raise InvalidFieldError("icon ist nur für Ordnerklassen (applies_to='folder') zulässig")


def _validate_kennzeichen_format(applies_to: str, kennzeichen_format: str | None) -> None:
    if kennzeichen_format is None:
        return
    if applies_to != "document":
        raise InvalidFieldError(
            "kennzeichen_format ist nur für Dokumentklassen (applies_to='document') zulässig"
        )
    used = set(_PLACEHOLDER_RE.findall(kennzeichen_format))
    unknown = sorted(used - KENNZEICHEN_PLACEHOLDERS)
    if unknown:
        raise InvalidFieldError(f"kennzeichen_format enthält unbekannte Platzhalter: {unknown}")
    if "Laufende_Nummer" not in used:
        raise InvalidFieldError(
            "kennzeichen_format muss den Platzhalter {Laufende_Nummer} enthalten"
        )


def _validate_kennzeichen_display_override(applies_to: str, value: bool | None) -> None:
    if value is not None and applies_to != "document":
        raise InvalidFieldError(
            "kennzeichen_display_override ist nur für Dokumentklassen "
            "(applies_to='document') zulässig"
        )


async def create_object_type(session: AsyncSession, payload: ObjectTypeCreate) -> ObjectType:
    existing = await session.execute(select(ObjectType).where(ObjectType.name == payload.name))
    if existing.scalar_one_or_none() is not None:
        raise DuplicateNameError(f"Objekttyp {payload.name!r} existiert bereits")
    await _validate_allowed_parent_types(session, payload.allowed_parent_types)
    _validate_icon(payload.applies_to, payload.icon)
    _validate_kennzeichen_format(payload.applies_to, payload.kennzeichen_format)
    _validate_kennzeichen_display_override(payload.applies_to, payload.kennzeichen_display_override)

    now = datetime.now(UTC)
    object_type = ObjectType(
        name=payload.name,
        applies_to=payload.applies_to,
        attributes=payload.attributes,
        naming_constraints=payload.naming_constraints,
        conditions=payload.conditions,
        allowed_parent_types=payload.allowed_parent_types,
        icon=payload.icon,
        kennzeichen_format=payload.kennzeichen_format,
        kennzeichen_display_override=payload.kennzeichen_display_override,
        created_at=now,
        updated_at=now,
    )
    session.add(object_type)
    await session.flush()
    return object_type


async def get_object_type(session: AsyncSession, object_type_id: int) -> ObjectType:
    object_type = await session.get(ObjectType, object_type_id)
    if object_type is None:
        raise NotFoundError(f"object_type_id {object_type_id!r} unbekannt")
    return object_type


async def list_object_types(
    session: AsyncSession, *, applies_to: str | None = None
) -> list[ObjectType]:
    query = select(ObjectType)
    if applies_to is not None:
        query = query.where(ObjectType.applies_to == applies_to)
    result = await session.execute(query.order_by(ObjectType.name))
    return list(result.scalars().all())


async def update_object_type(
    session: AsyncSession, object_type_id: int, payload: ObjectTypeUpdate
) -> ObjectType:
    object_type = await get_object_type(session, object_type_id)
    await _validate_allowed_parent_types(session, payload.allowed_parent_types)
    _validate_icon(object_type.applies_to, payload.icon)
    _validate_kennzeichen_format(object_type.applies_to, payload.kennzeichen_format)
    _validate_kennzeichen_display_override(
        object_type.applies_to, payload.kennzeichen_display_override
    )
    object_type.attributes = payload.attributes
    object_type.naming_constraints = payload.naming_constraints
    object_type.conditions = payload.conditions
    object_type.allowed_parent_types = payload.allowed_parent_types
    object_type.icon = payload.icon
    object_type.kennzeichen_format = payload.kennzeichen_format
    object_type.kennzeichen_display_override = payload.kennzeichen_display_override
    object_type.updated_at = datetime.now(UTC)
    await session.flush()
    return object_type


async def delete_object_type(session: AsyncSession, object_type_id: int) -> None:
    object_type = await get_object_type(session, object_type_id)
    await session.delete(object_type)
    await session.flush()


def _validate_layout_attributes(object_type: ObjectType, payload: LayoutIn) -> None:
    """Ein Layout darf nur Attribute referenzieren, die auch tatsächlich zum
    Objekttyp gehören (2.2b) - analog zur Referenzprüfung von
    ``allowedParentTypes`` (2.2a), verhindert verwaiste Feldverweise nach
    Tippfehlern oder nachträglich entfernten Attributen."""
    known = {attribute["name"] for attribute in object_type.attributes}
    referenced = {field.attribute for row in payload.rows for field in row.columns}
    unknown = referenced - known
    if unknown:
        raise InvalidFieldError(
            f"Layout referenziert unbekannte Attribute von {object_type.name!r}: {sorted(unknown)}"
        )


async def get_layout(
    session: AsyncSession, object_type_id: int, purpose: str
) -> ObjectTypeLayout | None:
    return await session.get(ObjectTypeLayout, (object_type_id, purpose))


async def upsert_layout(
    session: AsyncSession, object_type_id: int, purpose: str, payload: LayoutIn
) -> ObjectTypeLayout:
    object_type = await get_object_type(session, object_type_id)
    _validate_layout_attributes(object_type, payload)
    layout_dict = {
        "rows": [row.model_dump() for row in payload.rows],
        "responsive_breakpoint_px": payload.responsive_breakpoint_px,
    }
    now = datetime.now(UTC)
    existing = await get_layout(session, object_type_id, purpose)
    if existing is not None:
        existing.layout = layout_dict
        existing.updated_at = now
        await session.flush()
        return existing
    layout_row = ObjectTypeLayout(
        object_type_id=object_type_id,
        purpose=purpose,
        layout=layout_dict,
        created_at=now,
        updated_at=now,
    )
    session.add(layout_row)
    await session.flush()
    return layout_row


async def delete_layout(session: AsyncSession, object_type_id: int, purpose: str) -> None:
    """Setzt ein Layout auf das generierte Smart-Layout zurück (2.2b) -
    idempotent, da das Fehlen einer Override-Zeile bereits dem Default
    entspricht (kein Fehler, falls nie eine Abweichung gespeichert wurde)."""
    existing = await get_layout(session, object_type_id, purpose)
    if existing is not None:
        await session.delete(existing)
        await session.flush()


def _render_kennzeichen(
    format_str: str, *, jahr: int, monat: int, tag: int, laufende_nummer: int
) -> str:
    values = {
        "YYYY": f"{jahr:04d}",
        "YY": f"{jahr % 100:02d}",
        "MM": f"{monat:02d}",
        "DD": f"{tag:02d}",
        "Laufende_Nummer": f"{laufende_nummer:03d}",
    }
    return format_str.format(**values)


async def _next_sequence_number(session: AsyncSession, object_type_id: int, jahr: int) -> int:
    """Atomarer, gegen Nebenläufigkeit abgesicherter Jahres-Zähler (P5e-S1).
    ``INSERT ... ON CONFLICT DO NOTHING`` legt die Zähler-Zeile bei Bedarf an,
    ohne dass zwei gleichzeitige Erstaufrufe an einem Unique-Constraint
    scheitern; das anschließende ``SELECT ... FOR UPDATE`` sperrt die (jetzt
    garantiert existierende) Zeile für die restliche Transaktion, sodass
    parallele Aufrufe serialisiert statt gleichzeitig gelesen/geschrieben
    werden."""
    insert_stmt = (
        pg_insert(ObjectTypeSequence)
        .values(object_type_id=object_type_id, jahr=jahr, naechste_nummer=1)
        .on_conflict_do_nothing(index_elements=["object_type_id", "jahr"])
    )
    await session.execute(insert_stmt)

    result = await session.execute(
        select(ObjectTypeSequence)
        .where(
            ObjectTypeSequence.object_type_id == object_type_id,
            ObjectTypeSequence.jahr == jahr,
        )
        .with_for_update()
    )
    row = result.scalar_one()
    assigned = row.naechste_nummer
    row.naechste_nummer = assigned + 1
    await session.flush()
    return assigned


async def generate_next_kennzeichen(session: AsyncSession, object_type_id: int) -> str:
    object_type = await get_object_type(session, object_type_id)
    if object_type.kennzeichen_format is None:
        raise NoKennzeichenFormatError(
            f"object_type_id {object_type_id!r} hat keinen Kennzeichengenerator konfiguriert"
        )
    now = datetime.now(UTC)
    laufende_nummer = await _next_sequence_number(session, object_type_id, now.year)
    return _render_kennzeichen(
        object_type.kennzeichen_format,
        jahr=now.year,
        monat=now.month,
        tag=now.day,
        laufende_nummer=laufende_nummer,
    )


async def get_kennzeichen_config(session: AsyncSession) -> KennzeichenConfig:
    """Liest die (einzige) globale Anzeige-Konfiguration, legt sie mit Default
    an, falls sie noch nie gespeichert wurde (frischer Service, vor dem ersten
    `PUT /kennzeichen-config`) - gleiches Muster wie `OcrConfig`/`UploadConfig`
    der anderen Services."""
    config = await session.get(KennzeichenConfig, _KENNZEICHEN_CONFIG_ID)
    if config is None:
        config = KennzeichenConfig(
            id=_KENNZEICHEN_CONFIG_ID, show_before_filename=True, updated_at=datetime.now(UTC)
        )
        session.add(config)
        await session.flush()
    return config


async def update_kennzeichen_config(
    session: AsyncSession, *, show_before_filename: bool
) -> KennzeichenConfig:
    config = await get_kennzeichen_config(session)
    config.show_before_filename = show_before_filename
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config
