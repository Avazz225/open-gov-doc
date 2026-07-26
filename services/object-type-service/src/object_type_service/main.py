from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_constraint_engine import validate as run_validation
from dms_db_base import build_engine, make_session_factory
from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from object_type_service import repository
from object_type_service.models import Base, ObjectType
from object_type_service.schemas import (
    ObjectTypeCreate,
    ObjectTypeOut,
    ObjectTypeUpdate,
    ValidateRequest,
    ValidateResult,
)
from object_type_service.settings import Settings

settings = Settings()
configure_logging(settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS object_type"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    yield

    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.post("/object-types", response_model=ObjectTypeOut, status_code=status.HTTP_201_CREATED)
async def create_object_type(
    payload: ObjectTypeCreate, session: AsyncSession = Depends(get_session)
) -> ObjectTypeOut:
    try:
        object_type = await repository.create_object_type(session, payload)
    except repository.DuplicateNameError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return object_type


@app.get("/object-types", response_model=list[ObjectTypeOut])
async def list_object_types(
    applies_to: str | None = None, session: AsyncSession = Depends(get_session)
) -> list[ObjectTypeOut]:
    return await repository.list_object_types(session, applies_to=applies_to)


@app.get("/object-types/{object_type_id}", response_model=ObjectTypeOut)
async def get_object_type(
    object_type_id: int, session: AsyncSession = Depends(get_session)
) -> ObjectTypeOut:
    try:
        return await repository.get_object_type(session, object_type_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/object-types/{object_type_id}", response_model=ObjectTypeOut)
async def update_object_type(
    object_type_id: int, payload: ObjectTypeUpdate, session: AsyncSession = Depends(get_session)
) -> ObjectTypeOut:
    try:
        object_type = await repository.update_object_type(session, object_type_id, payload)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return object_type


@app.delete("/object-types/{object_type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_object_type(
    object_type_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    try:
        await repository.delete_object_type(session, object_type_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()


@app.post("/object-types/{object_type_id}/validate", response_model=ValidateResult)
async def validate_against_object_type(
    object_type_id: int, payload: ValidateRequest, session: AsyncSession = Depends(get_session)
) -> ValidateResult:
    try:
        object_type: ObjectType = await repository.get_object_type(session, object_type_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    schema = {
        "attributes": object_type.attributes,
        "namingConstraints": object_type.naming_constraints,
        "conditions": object_type.conditions,
    }
    errors = run_validation(schema, name=payload.name, attributes=payload.attributes)
    return ValidateResult(valid=not errors, errors=errors)
