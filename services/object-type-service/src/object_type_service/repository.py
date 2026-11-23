import re
from datetime import UTC, datetime

from dms_constraint_engine import (
    REQUIRE_PREFIX,
    ROOT_PARENT_TYPE,
    allowed_children,
    parse_condition,
    validate_child,
    validate_parent,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from object_type_service.models import (
    KennzeichenConfig,
    ObjectType,
    ObjectTypeLayout,
    ObjectTypeSequence,
    StructureConfig,
)
from object_type_service.schemas import (
    LayoutIn,
    ObjectTypeCreate,
    ObjectTypeUpdate,
    StructureCheckRequest,
    StructureConfigIn,
)

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
_STRUCTURE_CONFIG_ID = 1
STRUCTURE_FIELDS = (
    "allowed_child_folder_types",
    "allowed_document_types",
    "default_child_folder_type",
    "default_document_type",
    "children_from_template_only",
)
ROOT_LABEL = "Ablage (oberste Ebene)"


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


async def _validate_type_names(
    session: AsyncSession, field: str, names: list[str], applies_to: str
) -> None:
    """Structure lists and defaults (P91-S2a) may only name existing types
    of the right kind - folder classes for child folders, document types for
    documents."""
    if not names:
        return
    result = await session.execute(
        select(ObjectType.name, ObjectType.applies_to).where(ObjectType.name.in_(set(names)))
    )
    found = dict(result.all())
    missing = sorted(set(names) - found.keys())
    if missing:
        raise InvalidFieldError(f"{field} referenziert unbekannte Objekttypen: {missing}")
    wrong = sorted(name for name, kind in found.items() if kind != applies_to)
    if wrong:
        raise InvalidFieldError(
            f"{field} darf nur Typen mit applies_to='{applies_to}' nennen: {wrong}"
        )


async def _validate_structure_fields(session: AsyncSession, applies_to: str, values: dict) -> None:
    """Parent-side structure fields (P91-S2a, ADR 0255): folder classes only;
    names must exist with the right kind; a default must be admitted by its
    own list when one is set."""
    if applies_to != "folder":
        set_fields = [f for f in STRUCTURE_FIELDS if values.get(f) not in (None, False)]
        if set_fields:
            raise InvalidFieldError(f"{', '.join(set_fields)} gilt nur für Ordnerklassen")
        return
    for field, kind in (
        ("allowed_child_folder_types", "folder"),
        ("allowed_document_types", "document"),
    ):
        await _validate_type_names(session, field, values.get(field) or [], kind)
    for default_field, list_field, kind in (
        ("default_child_folder_type", "allowed_child_folder_types", "folder"),
        ("default_document_type", "allowed_document_types", "document"),
    ):
        default = values.get(default_field)
        if default is None:
            continue
        await _validate_type_names(session, default_field, [default], kind)
        allowed = values.get(list_field)
        if allowed is not None and default not in allowed:
            raise InvalidFieldError(
                f"{default_field} {default!r} ist in {list_field} nicht erlaubt"
            )


def _validate_status_transitions(attributes: list[dict], status_transitions: list[dict]) -> None:
    """``statusTransitions`` entries (4.5/7.1, Phase 45 Session 4) may only
    reference attribute names that actually belong to this object type in
    ``requiredAttributes`` - same "no orphaned field references" principle
    as `_validate_layout_attributes` (2.2b) - and each entry needs a
    non-empty, distinct `from`/`to` pair (a transition to itself is not a
    transition)."""
    if not status_transitions:
        return
    known = {attribute["name"] for attribute in attributes}
    for transition in status_transitions:
        from_status = transition.get("from")
        to_status = transition.get("to")
        if not from_status or not to_status:
            raise InvalidFieldError(
                f"statusTransitions-Eintrag benötigt sowohl 'from' als auch 'to': {transition!r}"
            )
        if from_status == to_status:
            raise InvalidFieldError(
                f"statusTransitions-Eintrag hat identisches 'from'/'to': {from_status!r}"
            )
        unknown = set(transition.get("requiredAttributes", [])) - known
        if unknown:
            raise InvalidFieldError(
                f"statusTransitions referenziert unbekannte Attribute: {sorted(unknown)}"
            )


# Embedded-metadata sources (P90-S5, ADR 0258) - one vocabulary for PDF
# (info dictionary and XMP) and Office/ODF (core properties); document-
# service's `embedded_metadata.py` fills exactly these keys.
EMBEDDED_METADATA_SOURCES = frozenset(
    {
        "title",
        "subject",
        "author",
        "keywords",
        "description",
        "category",
        "language",
        "created",
        "modified",
        "last_modified_by",
        "application",
    }
)
_DATE_SOURCES = frozenset({"created", "modified"})
_MAPPING_TARGET_TYPES = frozenset({"string", "date"})
_OVERWRITE_POLICIES = frozenset({"if_empty", "always"})


def _validate_metadata_mappings(
    applies_to: str, attributes: list[dict], mappings: list[dict]
) -> None:
    """P90-S5: `metadataMappings` entries name a known source, an attribute of
    this type (string, or date for the two date sources) and an overwrite
    policy; each attribute is filled from at most one source. Document types
    only - folders have no file."""
    if not mappings:
        return
    if applies_to != "document":
        raise InvalidFieldError("metadataMappings gibt es nur für Dokumenttypen")
    types = {attribute["name"]: attribute.get("type", "string") for attribute in attributes}
    seen: set[str] = set()
    for mapping in mappings:
        source, attribute = mapping.get("source"), mapping.get("attribute")
        overwrite = mapping.get("overwrite", "if_empty")
        if source not in EMBEDDED_METADATA_SOURCES:
            raise InvalidFieldError(
                f"metadataMappings: unbekannte Quelle {source!r} "
                f"(erlaubt: {sorted(EMBEDDED_METADATA_SOURCES)})"
            )
        if attribute not in types:
            raise InvalidFieldError(f"metadataMappings: unbekanntes Attribut {attribute!r}")
        target_type = types[attribute]
        if target_type not in _MAPPING_TARGET_TYPES or (
            target_type == "date" and source not in _DATE_SOURCES
        ):
            raise InvalidFieldError(
                f"metadataMappings: {source!r} passt nicht zum Attributtyp {target_type!r} "
                f"von {attribute!r}"
            )
        if overwrite not in _OVERWRITE_POLICIES:
            raise InvalidFieldError(
                "metadataMappings: overwrite muss 'if_empty' oder 'always' sein, "
                f"nicht {overwrite!r}"
            )
        if attribute in seen:
            raise InvalidFieldError(f"metadataMappings: {attribute!r} ist mehrfach Ziel")
        seen.add(attribute)


_NAMING_KEYS = frozenset({"mustContain", "pattern"})


def _validate_naming_constraints(naming_constraints: dict | None) -> None:
    """P88-S3: `namingConstraints` is `{"mustContain": [str], "pattern":
    str}` (both optional). Checked for shape on save - an unknown key (a
    typo like "mustcontain") or a wrong type would otherwise be stored and
    simply never apply. References to undeclared attributes stay allowed,
    as before; admin-ui's guided form only offers declared ones."""
    if not naming_constraints:
        return
    unknown = set(naming_constraints) - _NAMING_KEYS
    if unknown:
        raise InvalidFieldError(
            f"namingConstraints kennt nur 'mustContain' und 'pattern', nicht: {sorted(unknown)}"
        )
    must_contain = naming_constraints.get("mustContain", [])
    if not isinstance(must_contain, list) or not all(
        isinstance(name, str) and name.strip() for name in must_contain
    ):
        raise InvalidFieldError("namingConstraints.mustContain muss eine Liste von Namen sein")
    pattern = naming_constraints.get("pattern")
    if pattern is not None and not isinstance(pattern, str):
        raise InvalidFieldError("namingConstraints.pattern muss ein Text sein")


def _validate_conditions(conditions: list[dict]) -> None:
    """P88-S3: each condition is `{"if": "<attribute> <op> <value>", "then":
    "require:<attribute>"}` - the only shape `dms_constraint_engine`
    evaluates. A condition it can't parse used to be stored and then never
    fire (or, without an `if`, crash `validate()`), so it is refused on
    save."""
    for condition in conditions:
        if not isinstance(condition, dict) or set(condition) != {"if", "then"}:
            raise InvalidFieldError(
                f"conditions-Eintrag braucht genau 'if' und 'then': {condition!r}"
            )
        if not isinstance(condition["if"], str) or parse_condition(condition["if"]) is None:
            raise InvalidFieldError(
                f"conditions-Bedingung nicht auswertbar: {condition['if']!r} "
                "(erwartet '<Attribut> <Operator> <Wert>')"
            )
        then = condition["then"]
        if (
            not isinstance(then, str)
            or not then.startswith(REQUIRE_PREFIX)
            or not then.removeprefix(REQUIRE_PREFIX).strip()
        ):
            raise InvalidFieldError(
                f"conditions-Folge muss 'require:<Attribut>' sein, nicht: {then!r}"
            )


_REFERENCE_TARGETS = ("document", "folder")


def _validate_reference_targets(attributes: list[dict]) -> None:
    """P64-S1 (4.5): a `type: "reference"` attribute may optionally carry
    `reference_target` naming WHAT it references - `"document"` or
    `"folder"`, the only two instance-holding services that call this
    engine (`dms_constraint_engine` itself stays a pure, stateless library
    per ADR 0003 and never resolves it; `document-service`/`folder-service`
    perform the actual existence check using this field once `/validate`
    has confirmed the value's own shape). Omitting `reference_target`
    preserves the pre-existing, still-supported behavior of a pure
    format-only check (non-empty string) - not every reference is
    necessarily to a document or folder, and a caller not ready to declare
    a target isn't forced to. This function only checks the DEFINITION
    (object-type save time), never a value - value-shape checking for
    `type: "reference"` remains entirely in `dms_constraint_engine`."""
    for attribute in attributes:
        if attribute.get("type") != "reference":
            continue
        target = attribute.get("reference_target")
        if target is not None and target not in _REFERENCE_TARGETS:
            raise InvalidFieldError(
                f"reference_target von Attribut {attribute.get('name')!r} muss "
                f"eines von {_REFERENCE_TARGETS} sein, nicht {target!r}"
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
    _validate_status_transitions(payload.attributes, payload.status_transitions)
    _validate_naming_constraints(payload.naming_constraints)
    _validate_conditions(payload.conditions)
    _validate_reference_targets(payload.attributes)
    _validate_metadata_mappings(payload.applies_to, payload.attributes, payload.metadata_mappings)
    structure = {field: getattr(payload, field) for field in STRUCTURE_FIELDS}
    await _validate_structure_fields(session, payload.applies_to, structure)

    now = datetime.now(UTC)
    object_type = ObjectType(
        name=payload.name,
        applies_to=payload.applies_to,
        attributes=payload.attributes,
        naming_constraints=payload.naming_constraints,
        conditions=payload.conditions,
        allowed_parent_types=payload.allowed_parent_types,
        status_transitions=payload.status_transitions,
        icon=payload.icon,
        kennzeichen_format=payload.kennzeichen_format,
        kennzeichen_display_override=payload.kennzeichen_display_override,
        required_signature_level=payload.required_signature_level,
        default_retention_days=payload.default_retention_days,
        deletion_reason_required_override=payload.deletion_reason_required_override,
        default_archive_after_days=payload.default_archive_after_days,
        archive_encryption_enabled=payload.archive_encryption_enabled,
        classification_level=payload.classification_level,
        metadata_mappings=payload.metadata_mappings,
        **structure,
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
    _validate_status_transitions(payload.attributes, payload.status_transitions)
    _validate_naming_constraints(payload.naming_constraints)
    _validate_conditions(payload.conditions)
    _validate_reference_targets(payload.attributes)
    # `None` keeps the stored mappings; they are re-checked against the new
    # attribute list either way (removing an attribute a mapping targets is
    # an error, not a silent orphan).
    mappings = (
        payload.metadata_mappings
        if payload.metadata_mappings is not None
        else object_type.metadata_mappings or []
    )
    _validate_metadata_mappings(object_type.applies_to, payload.attributes, mappings)
    # Structure fields (P91-S2a): only those actually sent change.
    structure = {
        field: (
            getattr(payload, field)
            if field in payload.model_fields_set
            else getattr(object_type, field)
        )
        for field in STRUCTURE_FIELDS
    }
    if structure["children_from_template_only"] is None:
        structure["children_from_template_only"] = False
    await _validate_structure_fields(session, object_type.applies_to, structure)
    for field, value in structure.items():
        setattr(object_type, field, value)
    object_type.metadata_mappings = mappings
    object_type.attributes = payload.attributes
    object_type.naming_constraints = payload.naming_constraints
    object_type.conditions = payload.conditions
    object_type.allowed_parent_types = payload.allowed_parent_types
    object_type.status_transitions = payload.status_transitions
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


# --- Enforced filing structure (P91-S2a, ADR 0255) ---


async def get_structure_config(session: AsyncSession) -> StructureConfig:
    config = await session.get(StructureConfig, _STRUCTURE_CONFIG_ID)
    if config is None:
        await session.execute(
            pg_insert(StructureConfig)
            .values(id=_STRUCTURE_CONFIG_ID, enforcement="off", updated_at=datetime.now(UTC))
            .on_conflict_do_nothing(index_elements=["id"])
        )
        config = await session.get(StructureConfig, _STRUCTURE_CONFIG_ID)
    return config


async def update_structure_config(
    session: AsyncSession, payload: StructureConfigIn
) -> StructureConfig:
    config = await get_structure_config(session)
    fields = payload.model_fields_set
    if "root_allowed_folder_types" in fields:
        await _validate_type_names(
            session, "root_allowed_folder_types", payload.root_allowed_folder_types or [], "folder"
        )
        config.root_allowed_folder_types = payload.root_allowed_folder_types
    if "root_allowed_document_types" in fields:
        await _validate_type_names(
            session,
            "root_allowed_document_types",
            payload.root_allowed_document_types or [],
            "document",
        )
        config.root_allowed_document_types = payload.root_allowed_document_types
    if "enforcement" in fields and payload.enforcement is not None:
        config.enforcement = payload.enforcement
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


def _parent_rules(parent: ObjectType | None) -> dict | None:
    if parent is None or parent.applies_to != "folder":
        return None
    return {
        "label": parent.name,
        "allowedChildFolderTypes": parent.allowed_child_folder_types,
        "allowedDocumentTypes": parent.allowed_document_types,
        "childrenFromTemplateOnly": parent.children_from_template_only,
    }


def _root_rules(config: StructureConfig) -> dict:
    return {
        "label": ROOT_LABEL,
        "allowedChildFolderTypes": config.root_allowed_folder_types,
        "allowedDocumentTypes": config.root_allowed_document_types,
        "childrenFromTemplateOnly": False,
    }


async def _resolve_parent(
    session: AsyncSession, parent_object_type_id: int | None, parent_is_root: bool
) -> tuple[dict | None, str | None, ObjectType | None]:
    """Parent rules, the name the child-side rule compares against, and the
    parent type itself (for its defaults)."""
    if parent_is_root:
        return _root_rules(await get_structure_config(session)), ROOT_PARENT_TYPE, None
    if parent_object_type_id is None:
        return None, None, None
    parent = await session.get(ObjectType, parent_object_type_id)
    if parent is None:
        return None, None, None
    return _parent_rules(parent), parent.name, parent


def _child_side_schema(child: ObjectType) -> dict:
    return {"allowedParentTypes": child.allowed_parent_types}


async def check_structure(session: AsyncSession, payload: StructureCheckRequest) -> dict:
    """Both sides for one new child. With enforcement "off" nothing is
    checked here (the child-side rule still runs in `/validate`, as before).
    An untyped child below a parent with a default takes the default when
    the default is itself admitted; the caller then creates it typed."""
    config = await get_structure_config(session)
    result = {"enforcement": config.enforcement, "errors": [], "default_object_type_id": None}
    if config.enforcement == "off":
        return result
    rules, parent_name, parent = await _resolve_parent(
        session, payload.parent_object_type_id, payload.parent_is_root
    )
    child: ObjectType | None = None
    if payload.child_object_type_id is not None:
        child = await session.get(ObjectType, payload.child_object_type_id)
    elif parent is not None:
        default_name = (
            parent.default_child_folder_type
            if payload.child_kind == "folder"
            else parent.default_document_type
        )
        if default_name:
            found = await session.execute(select(ObjectType).where(ObjectType.name == default_name))
            child = found.scalar_one_or_none()
            if child is not None and child.applies_to == payload.child_kind:
                result["default_object_type_id"] = child.id
            else:
                child = None
    errors = validate_child(
        rules,
        payload.child_kind,
        child.name if child is not None else None,
        via_template=payload.via_template,
    )
    if child is not None:
        errors += validate_parent(_child_side_schema(child), parent_name)
    if errors and result["default_object_type_id"] is not None:
        # A default that isn't itself admitted is no help: report the
        # untyped child's own problem instead.
        result["default_object_type_id"] = None
        errors = validate_child(rules, payload.child_kind, None, via_template=payload.via_template)
    result["errors"] = errors
    return result


async def list_allowed_children(
    session: AsyncSession, parent_object_type_id: int | None, parent_is_root: bool
) -> dict:
    """For "Neuer Ordner"/upload forms (used by the UIs in P91-S3)."""
    config = await get_structure_config(session)
    rules, parent_name, parent = await _resolve_parent(
        session, parent_object_type_id, parent_is_root
    )
    if config.enforcement == "off":
        rules = None  # only the child-side rules apply
    types = await list_object_types(session)
    candidates = [
        {"name": t.name, "appliesTo": t.applies_to, "allowedParentTypes": t.allowed_parent_types}
        for t in types
    ]
    computed = allowed_children(rules, parent_name, candidates)
    by_name = {t.name: t for t in types}
    return {
        "enforcement": config.enforcement,
        "folder_types": [{"id": by_name[n].id, "name": n} for n in computed["folder_types"]],
        "document_types": [{"id": by_name[n].id, "name": n} for n in computed["document_types"]],
        "untyped_folders": computed["untyped_folders"],
        "untyped_documents": computed["untyped_documents"],
        "children_from_template_only": computed["children_from_template_only"],
        "default_child_folder_type": parent.default_child_folder_type if parent else None,
        "default_document_type": parent.default_document_type if parent else None,
    }
