from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from folder_service import repository
from folder_service.models import Base
from folder_service.object_type_client import ObjectTypeClient
from folder_service.schemas import FolderCreate, FolderOut, FolderUpdate
from folder_service.settings import Settings

settings = Settings()
configure_logging(settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS folder"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    async with app.state.session_factory() as session:
        await repository.ensure_root_folder(session)
        await session.commit()

    app.state.object_type_client = ObjectTypeClient(settings.object_type_service_base_url)

    event_bus = NatsEventBusClient(settings.nats_url, stream="folder")
    await event_bus.connect()
    app.state.event_bus = event_bus

    yield

    await event_bus.close()
    await app.state.object_type_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


async def publish_event(event_type: str, subject: str, payload: dict) -> None:
    event = Event(
        event_type=event_type, service_name=settings.service_name, subject=subject, payload=payload
    )
    await app.state.event_bus.publish(event_type, event.to_bytes())


async def _validate_against_object_type(object_type_id: int | None, *, name: str, attributes: dict):
    if object_type_id is None:
        return
    errors = await app.state.object_type_client.validate(
        object_type_id, name=name, attributes=attributes
    )
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.post("/folders", response_model=FolderOut, status_code=status.HTTP_201_CREATED)
async def create_folder(
    payload: FolderCreate, session: AsyncSession = Depends(get_session)
) -> FolderOut:
    await _validate_against_object_type(
        payload.object_type_id, name=payload.name, attributes=payload.attributes
    )
    try:
        folder = await repository.create_folder(
            session,
            name=payload.name,
            parent_id=payload.parent_id,
            object_type_id=payload.object_type_id,
            attributes=payload.attributes,
            created_by=payload.created_by,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "folder.resource.created",
        subject=folder.id,
        payload={
            "resource_id": folder.id,
            "parent_id": folder.parent_id,
            "resource_type": "folder",
        },
    )
    return folder


@app.get("/folders/{folder_id}", response_model=FolderOut)
async def get_folder(folder_id: str, session: AsyncSession = Depends(get_session)) -> FolderOut:
    try:
        return await repository.get_folder(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/folders/{folder_id}/children", response_model=list[FolderOut])
async def list_children(
    folder_id: str, session: AsyncSession = Depends(get_session)
) -> list[FolderOut]:
    try:
        return await repository.list_children(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/folders/{folder_id}", response_model=FolderOut)
async def update_folder(
    folder_id: str, payload: FolderUpdate, session: AsyncSession = Depends(get_session)
) -> FolderOut:
    try:
        folder, moved = await repository.update_folder(
            session,
            folder_id,
            name=payload.name,
            new_parent_id=payload.parent_id,
            attributes=payload.attributes,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    if moved:
        await publish_event(
            "folder.resource.moved",
            subject=folder.id,
            payload={"resource_id": folder.id, "new_parent_id": folder.parent_id},
        )
    return folder


@app.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(folder_id: str, session: AsyncSession = Depends(get_session)) -> None:
    try:
        await repository.delete_folder(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.FolderNotEmptyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "folder.resource.deleted", subject=folder_id, payload={"resource_id": folder_id}
    )
