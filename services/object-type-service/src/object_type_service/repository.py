from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from object_type_service.models import ObjectType
from object_type_service.schemas import ObjectTypeCreate, ObjectTypeUpdate


class NotFoundError(Exception):
    pass


class DuplicateNameError(Exception):
    pass


async def create_object_type(session: AsyncSession, payload: ObjectTypeCreate) -> ObjectType:
    existing = await session.execute(select(ObjectType).where(ObjectType.name == payload.name))
    if existing.scalar_one_or_none() is not None:
        raise DuplicateNameError(f"Objekttyp {payload.name!r} existiert bereits")

    now = datetime.now(UTC)
    object_type = ObjectType(
        name=payload.name,
        applies_to=payload.applies_to,
        attributes=payload.attributes,
        naming_constraints=payload.naming_constraints,
        conditions=payload.conditions,
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
    object_type.attributes = payload.attributes
    object_type.naming_constraints = payload.naming_constraints
    object_type.conditions = payload.conditions
    object_type.updated_at = datetime.now(UTC)
    await session.flush()
    return object_type


async def delete_object_type(session: AsyncSession, object_type_id: int) -> None:
    object_type = await get_object_type(session, object_type_id)
    await session.delete(object_type)
    await session.flush()
