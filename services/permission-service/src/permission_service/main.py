from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import NatsEventBusClient
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from permission_service import repository
from permission_service.models import Base, ResourceNode
from permission_service.schemas import (
    CheckResult,
    EffectivePermissionsOut,
    ResourceNodeOut,
    ResourceNodeUpdate,
    RoleAssignmentCreate,
    RoleAssignmentOut,
    RoleCreate,
    RoleOut,
)
from permission_service.settings import Settings
from permission_service.structure_consumer import start_consuming

settings = Settings()
configure_logging(settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS permission"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    async with app.state.session_factory() as session:
        await repository.ensure_root_resource(session)
        await session.commit()

    event_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await event_bus.connect()
    app.state.event_bus = event_bus
    await start_consuming(event_bus, settings.structure_subjects, app.state.session_factory)

    yield

    await event_bus.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.post("/roles", response_model=RoleOut)
async def create_role(payload: RoleCreate, session: AsyncSession = Depends(get_session)) -> RoleOut:
    role = await repository.create_role(
        session, payload.name, payload.description, payload.permissions
    )
    await session.commit()
    return role


@app.get("/roles", response_model=list[RoleOut])
async def list_roles(session: AsyncSession = Depends(get_session)) -> list[RoleOut]:
    return await repository.list_roles(session)


@app.post("/role-assignments", response_model=RoleAssignmentOut, status_code=201)
async def create_role_assignment(
    payload: RoleAssignmentCreate, session: AsyncSession = Depends(get_session)
) -> RoleAssignmentOut:
    try:
        assignment = await repository.create_role_assignment(
            session,
            principal_type=payload.principal_type,
            principal_id=payload.principal_id,
            role_id=payload.role_id,
            resource_id=payload.resource_id,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return assignment


@app.delete("/role-assignments/{assignment_id}", status_code=204)
async def delete_role_assignment(
    assignment_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    try:
        await repository.delete_role_assignment(session, assignment_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()


@app.get("/resources/{resource_id}", response_model=ResourceNodeOut)
async def get_resource(
    resource_id: str, session: AsyncSession = Depends(get_session)
) -> ResourceNodeOut:
    resource = await session.get(ResourceNode, resource_id)
    if resource is None:
        raise HTTPException(status_code=404, detail=f"resource_id {resource_id!r} unbekannt")
    return resource


@app.patch("/resources/{resource_id}", response_model=ResourceNodeOut)
async def update_resource(
    resource_id: str, payload: ResourceNodeUpdate, session: AsyncSession = Depends(get_session)
) -> ResourceNodeOut:
    try:
        resource = await repository.set_resource_inherit(session, resource_id, payload.inherit)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return resource


@app.get(
    "/effective-permissions/{principal_id}/{resource_id}", response_model=EffectivePermissionsOut
)
async def effective_permissions(
    principal_id: str, resource_id: str, session: AsyncSession = Depends(get_session)
) -> EffectivePermissionsOut:
    entry = await repository.get_effective_permissions(session, principal_id, resource_id)
    await session.commit()
    return EffectivePermissionsOut(
        principal_id=principal_id,
        resource_id=resource_id,
        roles=entry.roles,
        permissions=entry.permissions,
    )


@app.get("/check", response_model=CheckResult)
async def check(
    principal_id: str,
    resource_id: str,
    permission: str,
    session: AsyncSession = Depends(get_session),
) -> CheckResult:
    entry = await repository.get_effective_permissions(session, principal_id, resource_id)
    await session.commit()
    return CheckResult(allowed=permission in entry.permissions)
