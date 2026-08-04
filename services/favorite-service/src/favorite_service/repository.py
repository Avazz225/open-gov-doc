from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from favorite_service.models import Favorite
from favorite_service.schemas import FavoriteCreate


class NotFoundError(Exception):
    pass


class DuplicateError(Exception):
    pass


async def create_favorite(session: AsyncSession, payload: FavoriteCreate) -> Favorite:
    existing = await session.execute(
        select(Favorite).where(
            Favorite.user_id == payload.user_id,
            Favorite.object_type == payload.object_type,
            Favorite.object_id == payload.object_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise DuplicateError(
            f"{payload.object_type} {payload.object_id!r} ist für {payload.user_id!r} "
            "bereits favorisiert"
        )
    favorite = Favorite(
        user_id=payload.user_id,
        object_type=payload.object_type,
        object_id=payload.object_id,
        created_at=datetime.now(UTC),
    )
    session.add(favorite)
    await session.flush()
    return favorite


async def delete_favorite(
    session: AsyncSession, *, user_id: str, object_type: str, object_id: str
) -> None:
    result = await session.execute(
        select(Favorite).where(
            Favorite.user_id == user_id,
            Favorite.object_type == object_type,
            Favorite.object_id == object_id,
        )
    )
    favorite = result.scalar_one_or_none()
    if favorite is None:
        raise NotFoundError(f"{object_type} {object_id!r} ist für {user_id!r} nicht favorisiert")
    await session.delete(favorite)


async def list_favorites(
    session: AsyncSession, *, user_id: str, object_type: str | None = None
) -> list[Favorite]:
    query = select(Favorite).where(Favorite.user_id == user_id)
    if object_type is not None:
        query = query.where(Favorite.object_type == object_type)
    query = query.order_by(Favorite.created_at.desc())
    result = await session.execute(query)
    return list(result.scalars().all())
