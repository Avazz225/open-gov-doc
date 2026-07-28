from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_constraint_engine import ROOT_PARENT_TYPE
from dms_constraint_engine import validate as run_validation
from dms_db_base import build_engine, make_session_factory
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from object_type_service import repository
from object_type_service.layout import generate_smart_layout
from object_type_service.models import Base, ObjectType
from object_type_service.schemas import (
    LayoutIn,
    LayoutOut,
    LayoutPurpose,
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
        # Ad-hoc-Schema-Erweiterung (kein Alembic in dieser frühen Phase, siehe
        # CONTRIBUTING.md): `create_all` legt fehlende TABELLEN an, ändert aber
        # keine bestehenden - beide Spalten kamen erst in P5b-S1 dazu (2.2a).
        await conn.execute(
            text(
                "ALTER TABLE object_type.object_type "
                "ADD COLUMN IF NOT EXISTS allowed_parent_types JSON"
            )
        )
        await conn.execute(
            text("ALTER TABLE object_type.object_type ADD COLUMN IF NOT EXISTS icon VARCHAR(64)")
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    yield

    if registration:
        await registration.stop()
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
    except repository.InvalidFieldError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    except repository.InvalidFieldError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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

    # Auflösung der Platzierungs-Information (2.2a): der Aufrufer kennt nur die
    # object_type_id des Elternordners (bzw. dass er die Wurzel ist) - der Name
    # wird hier aufgelöst, damit Object-Type Service die einzige Quelle für
    # Objekttyp-Namen bleibt (kein zusätzlicher Roundtrip beim Aufrufer nötig).
    if payload.parent_is_root:
        parent_type_name = ROOT_PARENT_TYPE
    elif payload.parent_object_type_id is not None:
        parent_object_type = await session.get(ObjectType, payload.parent_object_type_id)
        parent_type_name = parent_object_type.name if parent_object_type is not None else None
    else:
        parent_type_name = None

    schema = {
        "attributes": object_type.attributes,
        "namingConstraints": object_type.naming_constraints,
        "conditions": object_type.conditions,
        "allowedParentTypes": object_type.allowed_parent_types,
    }
    errors = run_validation(
        schema, name=payload.name, attributes=payload.attributes, parent_type_name=parent_type_name
    )
    return ValidateResult(valid=not errors, errors=errors)


@app.get("/object-types/{object_type_id}/layouts/{purpose}", response_model=LayoutOut)
async def get_layout(
    object_type_id: int, purpose: LayoutPurpose, session: AsyncSession = Depends(get_session)
) -> LayoutOut:
    try:
        object_type = await repository.get_object_type(session, object_type_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    stored = await repository.get_layout(session, object_type_id, purpose.value)
    if stored is not None:
        return LayoutOut(**stored.layout, is_custom=True)
    generated = generate_smart_layout(object_type.attributes)
    return LayoutOut(**generated, is_custom=False)


@app.put("/object-types/{object_type_id}/layouts/{purpose}", response_model=LayoutOut)
async def put_layout(
    object_type_id: int,
    purpose: LayoutPurpose,
    payload: LayoutIn,
    session: AsyncSession = Depends(get_session),
) -> LayoutOut:
    try:
        layout_row = await repository.upsert_layout(session, object_type_id, purpose.value, payload)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.InvalidFieldError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return LayoutOut(**layout_row.layout, is_custom=True)


@app.delete(
    "/object-types/{object_type_id}/layouts/{purpose}", status_code=status.HTTP_204_NO_CONTENT
)
async def reset_layout(
    object_type_id: int, purpose: LayoutPurpose, session: AsyncSession = Depends(get_session)
) -> None:
    try:
        await repository.get_object_type(session, object_type_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await repository.delete_layout(session, object_type_id, purpose.value)
    await session.commit()
