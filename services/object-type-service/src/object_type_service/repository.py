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

# Reference number generator placeholders (P5e-S1) - see PROGRESS.md
# "Kennzeichengenerator". Date/counter placeholders are hard-wired; every
# other placeholder has been interpreted as an attribute name since P17-S2
# (14.2) (e.g. `{Federführung}`), see
# _validate_kennzeichen_format/_render_kennzeichen.
KENNZEICHEN_PLACEHOLDERS = {"YYYY", "YY", "MM", "DD", "Laufende_Nummer"}
# `\w` instead of `[A-Za-z_]`, so that Unicode attribute names (umlauts as in
# "Federführung") work as placeholders - Python identifiers/format() already
# support this anyway (PEP 3131), the old regex was narrower here than
# necessary.
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
_KENNZEICHEN_CONFIG_ID = 1


class NotFoundError(Exception):
    pass


class DuplicateNameError(Exception):
    pass


class NoKennzeichenFormatError(Exception):
    """No `kennzeichen_format` configured for this object type - to be
    treated as a 404 by the caller (P5e-S1)."""


class MissingKennzeichenAttributeError(Exception):
    """An attribute placeholder referenced in `kennzeichen_format` (P17-S2,
    14.2) received no value on creation - to be treated as a 422 by the
    caller. Only occurs if the referenced attribute is not marked as a
    required field (a required attribute would already have been enforced
    earlier by `object_type_client.validate()`)."""


class InvalidFieldError(Exception):
    """Business validation error on one of the new 2.2a fields
    (``allowed_parent_types``/``icon``) - to be treated as a 422 by the
    caller, unlike the 404/409 of the other repository errors."""


async def _validate_allowed_parent_types(
    session: AsyncSession, allowed_parent_types: list[str] | None
) -> None:
    """``allowedParentTypes`` (2.2a) may only reference ``"$ROOT"`` or already
    existing folder classes (``applies_to == "folder"``) - only folders can
    be parent objects. No retroactive check of existing storage locations if
    a referenced class is deleted later (see Concept 13, open point) - a then
    "dangling" reference merely means that the affected parent type can no
    longer be resolved on the next placement check and is treated like an
    unknown type."""
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


def _validate_kennzeichen_format(
    applies_to: str, kennzeichen_format: str | None, attributes: list[dict] | None = None
) -> None:
    if kennzeichen_format is None:
        return
    if applies_to != "document":
        raise InvalidFieldError(
            "kennzeichen_format ist nur für Dokumentklassen (applies_to='document') zulässig"
        )
    used = set(_PLACEHOLDER_RE.findall(kennzeichen_format))
    # Since P17-S2 (14.2): a placeholder that is not a date/counter
    # placeholder must reference an attribute actually defined on the object
    # type (e.g. {Federführung}) - not an arbitrary free name, otherwise a
    # typo would only surface on the first actual creation of a document
    # (KeyError, see _render_kennzeichen) instead of already when the format
    # is saved.
    attribute_names = {a["name"] for a in (attributes or []) if "name" in a}
    unknown = sorted(used - KENNZEICHEN_PLACEHOLDERS - attribute_names)
    if unknown:
        raise InvalidFieldError(
            f"kennzeichen_format enthält unbekannte Platzhalter (weder Datums-/"
            f"Zähler-Platzhalter noch ein Attribut dieses Objekttyps): {unknown}"
        )
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


def _validate_required_signature_level(applies_to: str, value: str | None) -> None:
    """Minimum signature level (3.10, since P6-S7) only makes sense for
    document classes - folders are not signed, same restriction as for the
    reference number generator."""
    if value is not None and applies_to != "document":
        raise InvalidFieldError(
            "required_signature_level ist nur für Dokumentklassen (applies_to='document') zulässig"
        )


def _validate_default_retention_days(value: int | None) -> None:
    """Retention (5.2, since P7-S1) applies equally to document AND folder
    classes (unlike reference number/signature) - no applies_to restriction,
    just a value range check."""
    if value is not None and value < 0:
        raise InvalidFieldError("default_retention_days darf nicht negativ sein")


def _validate_default_archive_after_days(value: int | None) -> None:
    """Records disposal (5.6, since P7-S3) - same pattern as
    default_retention_days, likewise valid for document and folder classes."""
    if value is not None and value < 0:
        raise InvalidFieldError("default_archive_after_days darf nicht negativ sein")


def _validate_classification_level(applies_to: str, value: str | None) -> None:
    """Classified-documents classification (2.5, P15-S1, multi-level since
    P17-S2) is, per the concept text, only intended for document classes -
    same restriction as for the reference number generator/signature level.
    The specific level itself is not checked here (Pydantic's `Literal` at
    the schema boundary already handles that, see ClassificationLevel)."""
    if value is not None and applies_to != "document":
        raise InvalidFieldError(
            "classification_level ist nur für Dokumentklassen (applies_to='document') zulässig"
        )


async def create_object_type(session: AsyncSession, payload: ObjectTypeCreate) -> ObjectType:
    existing = await session.execute(select(ObjectType).where(ObjectType.name == payload.name))
    if existing.scalar_one_or_none() is not None:
        raise DuplicateNameError(f"Objekttyp {payload.name!r} existiert bereits")
    await _validate_allowed_parent_types(session, payload.allowed_parent_types)
    _validate_icon(payload.applies_to, payload.icon)
    _validate_kennzeichen_format(payload.applies_to, payload.kennzeichen_format, payload.attributes)
    _validate_kennzeichen_display_override(payload.applies_to, payload.kennzeichen_display_override)
    _validate_required_signature_level(payload.applies_to, payload.required_signature_level)
    _validate_default_retention_days(payload.default_retention_days)
    _validate_default_archive_after_days(payload.default_archive_after_days)
    _validate_classification_level(payload.applies_to, payload.classification_level)

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
        required_signature_level=payload.required_signature_level,
        default_retention_days=payload.default_retention_days,
        deletion_reason_required_override=payload.deletion_reason_required_override,
        default_archive_after_days=payload.default_archive_after_days,
        archive_encryption_enabled=payload.archive_encryption_enabled,
        classification_level=payload.classification_level,
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
    session: AsyncSession, *, applies_to: str | None = None, is_classified: bool | None = None
) -> list[ObjectType]:
    """``is_classified`` remains the filter parameter name (callers, e.g.
    document-service, ask "some classification or none", not for a specific
    level) - translated internally, since P17-S2, to
    ``classification_level IS (NOT) NULL``."""
    query = select(ObjectType)
    if applies_to is not None:
        query = query.where(ObjectType.applies_to == applies_to)
    if is_classified is True:
        query = query.where(ObjectType.classification_level.is_not(None))
    elif is_classified is False:
        query = query.where(ObjectType.classification_level.is_(None))
    result = await session.execute(query.order_by(ObjectType.name))
    return list(result.scalars().all())


async def update_object_type(
    session: AsyncSession, object_type_id: int, payload: ObjectTypeUpdate
) -> ObjectType:
    object_type = await get_object_type(session, object_type_id)
    await _validate_allowed_parent_types(session, payload.allowed_parent_types)
    _validate_icon(object_type.applies_to, payload.icon)
    _validate_kennzeichen_format(
        object_type.applies_to, payload.kennzeichen_format, payload.attributes
    )
    _validate_kennzeichen_display_override(
        object_type.applies_to, payload.kennzeichen_display_override
    )
    _validate_required_signature_level(object_type.applies_to, payload.required_signature_level)
    _validate_default_retention_days(payload.default_retention_days)
    _validate_default_archive_after_days(payload.default_archive_after_days)
    _validate_classification_level(object_type.applies_to, payload.classification_level)
    object_type.attributes = payload.attributes
    object_type.naming_constraints = payload.naming_constraints
    object_type.conditions = payload.conditions
    object_type.allowed_parent_types = payload.allowed_parent_types
    object_type.icon = payload.icon
    object_type.kennzeichen_format = payload.kennzeichen_format
    object_type.kennzeichen_display_override = payload.kennzeichen_display_override
    object_type.required_signature_level = payload.required_signature_level
    object_type.default_retention_days = payload.default_retention_days
    object_type.deletion_reason_required_override = payload.deletion_reason_required_override
    object_type.default_archive_after_days = payload.default_archive_after_days
    object_type.archive_encryption_enabled = payload.archive_encryption_enabled
    object_type.classification_level = payload.classification_level
    object_type.updated_at = datetime.now(UTC)
    await session.flush()
    return object_type


async def delete_object_type(session: AsyncSession, object_type_id: int) -> None:
    object_type = await get_object_type(session, object_type_id)
    await session.delete(object_type)
    await session.flush()


def _validate_layout_attributes(object_type: ObjectType, payload: LayoutIn) -> None:
    """A layout may only reference attributes that actually belong to the
    object type (2.2b) - analogous to the reference check of
    ``allowedParentTypes`` (2.2a), prevents orphaned field references after
    typos or attributes removed later."""
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
    """Resets a layout to the generated smart layout (2.2b) - idempotent,
    since the absence of an override row already corresponds to the default
    (no error if no deviation was ever saved)."""
    existing = await get_layout(session, object_type_id, purpose)
    if existing is not None:
        await session.delete(existing)
        await session.flush()


def _render_kennzeichen(
    format_str: str,
    *,
    jahr: int,
    monat: int,
    tag: int,
    laufende_nummer: int,
    attribute_values: dict | None = None,
) -> str:
    values: dict = {
        "YYYY": f"{jahr:04d}",
        "YY": f"{jahr % 100:02d}",
        "MM": f"{monat:02d}",
        "DD": f"{tag:02d}",
        "Laufende_Nummer": f"{laufende_nummer:03d}",
    }
    # Attribute-based placeholders (P17-S2, 14.2, e.g. {Federführung}) -
    # `_validate_kennzeichen_format` already ensures, when the format is
    # saved, that every placeholder not covered here is an attribute of the
    # object type; if the value is still missing (attribute not marked as a
    # required field, see MissingKennzeichenAttributeError), `.format()``
    # below aborts with `KeyError` instead of silently producing an
    # incomplete reference number with an empty gap.
    for name, value in (attribute_values or {}).items():
        values[name] = "" if value is None else str(value)
    try:
        return format_str.format(**values)
    except KeyError as exc:
        raise MissingKennzeichenAttributeError(
            f"Für den Kennzeichen-Platzhalter {exc} wurde kein Attributwert übergeben"
        ) from exc


async def _next_sequence_number(session: AsyncSession, object_type_id: int, jahr: int) -> int:
    """Atomic, concurrency-safe yearly counter (P5e-S1). ``INSERT ... ON
    CONFLICT DO NOTHING`` creates the counter row if needed, without two
    simultaneous first calls failing on a unique constraint; the subsequent
    ``SELECT ... FOR UPDATE`` locks the (now guaranteed to exist) row for the
    rest of the transaction, so parallel calls are serialized instead of
    being read/written concurrently."""
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


async def generate_next_kennzeichen(
    session: AsyncSession, object_type_id: int, attribute_values: dict | None = None
) -> str:
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
        attribute_values=attribute_values,
    )


async def get_kennzeichen_config(session: AsyncSession) -> KennzeichenConfig:
    """Reads the (single) global display configuration, creates it with
    defaults if it has never been saved before (fresh service, before the
    first `PUT /kennzeichen-config`) - same pattern as
    `OcrConfig`/`UploadConfig` of the other services."""
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
