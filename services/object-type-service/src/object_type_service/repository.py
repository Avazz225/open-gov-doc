from datetime import UTC, datetime

from dms_constraint_engine import ROOT_PARENT_TYPE
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from object_type_service.models import ObjectType
from object_type_service.schemas import ObjectTypeCreate, ObjectTypeUpdate


class NotFoundError(Exception):
    pass


class DuplicateNameError(Exception):
    pass


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


async def create_object_type(session: AsyncSession, payload: ObjectTypeCreate) -> ObjectType:
    existing = await session.execute(select(ObjectType).where(ObjectType.name == payload.name))
    if existing.scalar_one_or_none() is not None:
        raise DuplicateNameError(f"Objekttyp {payload.name!r} existiert bereits")
    await _validate_allowed_parent_types(session, payload.allowed_parent_types)
    _validate_icon(payload.applies_to, payload.icon)

    now = datetime.now(UTC)
    object_type = ObjectType(
        name=payload.name,
        applies_to=payload.applies_to,
        attributes=payload.attributes,
        naming_constraints=payload.naming_constraints,
        conditions=payload.conditions,
        allowed_parent_types=payload.allowed_parent_types,
        icon=payload.icon,
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
    object_type.attributes = payload.attributes
    object_type.naming_constraints = payload.naming_constraints
    object_type.conditions = payload.conditions
    object_type.allowed_parent_types = payload.allowed_parent_types
    object_type.icon = payload.icon
    object_type.updated_at = datetime.now(UTC)
    await session.flush()
    return object_type


async def delete_object_type(session: AsyncSession, object_type_id: int) -> None:
    object_type = await get_object_type(session, object_type_id)
    await session.delete(object_type)
    await session.flush()
