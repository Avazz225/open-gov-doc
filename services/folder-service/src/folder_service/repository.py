import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from folder_service.models import Folder
from folder_service.settings import ROOT_FOLDER_ID


class NotFoundError(Exception):
    pass


class FolderNotEmptyError(Exception):
    pass


async def ensure_root_folder(session: AsyncSession) -> None:
    existing = await session.get(Folder, ROOT_FOLDER_ID)
    if existing is None:
        now = datetime.now(UTC)
        session.add(
            Folder(
                id=ROOT_FOLDER_ID,
                name="Root",
                parent_id=None,
                object_type_id=None,
                attributes={},
                created_by="system",
                created_at=now,
                updated_at=now,
            )
        )
        await session.flush()


async def get_folder(session: AsyncSession, folder_id: str) -> Folder:
    folder = await session.get(Folder, folder_id)
    if folder is None:
        raise NotFoundError(f"folder_id {folder_id!r} unbekannt")
    return folder


async def list_children(session: AsyncSession, folder_id: str) -> list[Folder]:
    await get_folder(session, folder_id)
    result = await session.execute(
        select(Folder).where(Folder.parent_id == folder_id).order_by(Folder.name)
    )
    return list(result.scalars().all())


async def create_folder(
    session: AsyncSession,
    *,
    name: str,
    parent_id: str,
    object_type_id: int | None,
    attributes: dict,
    created_by: str,
) -> Folder:
    await get_folder(session, parent_id)  # 404, falls Elternordner unbekannt

    now = datetime.now(UTC)
    folder = Folder(
        id=str(uuid.uuid4()),
        name=name,
        parent_id=parent_id,
        object_type_id=object_type_id,
        attributes=attributes,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    session.add(folder)
    await session.flush()
    return folder


async def update_folder(
    session: AsyncSession,
    folder_id: str,
    *,
    name: str | None,
    new_parent_id: str | None,
    attributes: dict | None,
) -> tuple[Folder, bool]:
    """Aktualisiert Name/Attribute und optional den Elternordner (Verschieben).
    Gibt zurück, ob sich der Elternordner tatsächlich geändert hat, damit der
    Aufrufer nur dann ein ``.resource.moved``-Event publiziert."""
    folder = await get_folder(session, folder_id)
    moved = False

    if new_parent_id is not None and new_parent_id != folder.parent_id:
        if new_parent_id == folder_id:
            raise ValueError("Ein Ordner kann nicht sein eigener Elternordner sein")
        await get_folder(session, new_parent_id)
        folder.parent_id = new_parent_id
        moved = True

    if name is not None:
        folder.name = name
    if attributes is not None:
        folder.attributes = attributes

    folder.updated_at = datetime.now(UTC)
    await session.flush()
    return folder, moved


async def delete_folder(session: AsyncSession, folder_id: str) -> None:
    folder = await get_folder(session, folder_id)
    children = await list_children(session, folder_id)
    if children:
        raise FolderNotEmptyError(f"Ordner {folder_id!r} enthält noch {len(children)} Unterordner")
    await session.delete(folder)
    await session.flush()
