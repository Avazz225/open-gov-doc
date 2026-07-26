from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
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
    ScopeLockCreate,
    ScopeLockOut,
    ScopeLockRelease,
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

    # Eigener Producer-Client für publizierte Ereignisse (Bereichssperren, 4.7/5.3) -
    # getrennt vom obigen reinen Konsumenten-Client (ensure_stream=False), da ein
    # Producer den eigenen Stream anlegen muss (siehe ADR 0001).
    publisher = NatsEventBusClient(settings.nats_url, stream="permission", ensure_stream=True)
    await publisher.connect()
    app.state.publisher = publisher

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    yield

    if registration:
        await registration.stop()
    await publisher.close()
    await event_bus.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


async def publish_event(event_type: str, payload: dict) -> None:
    event = Event(event_type=event_type, service_name=settings.service_name, payload=payload)
    await app.state.publisher.publish(event_type, event.to_bytes())


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


@app.get("/role-assignments", response_model=list[RoleAssignmentOut])
async def list_role_assignments(
    principal_id: str | None = None,
    resource_id: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[RoleAssignmentOut]:
    return await repository.list_role_assignments(
        session, principal_id=principal_id, resource_id=resource_id
    )


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
    access_type: Literal["read", "write"] = "write",
    session: AsyncSession = Depends(get_session),
) -> CheckResult:
    entry = await repository.get_effective_permissions(session, principal_id, resource_id)

    active_locks = await repository.get_active_scope_locks_for_resource(session, resource_id)
    blocking_locks = [lock for lock in active_locks if access_type == "write" or lock.blocks_read]
    if blocking_locks and "scope_lock.bypass" not in entry.permissions:
        blocking_lock = blocking_locks[0]
        await session.commit()
        return CheckResult(
            allowed=False,
            blocked_by_scope_lock=True,
            scope_lock_reason=blocking_lock.reason,
            scope_lock_expires_at=blocking_lock.expires_at,
        )

    await session.commit()
    return CheckResult(allowed=permission in entry.permissions)


@app.post("/scope-locks", response_model=ScopeLockOut, status_code=201)
async def create_scope_lock(
    payload: ScopeLockCreate, session: AsyncSession = Depends(get_session)
) -> ScopeLockOut:
    try:
        lock = await repository.create_scope_lock(
            session,
            resource_id=payload.resource_id,
            locked_by=payload.locked_by,
            reason=payload.reason,
            blocks_read=payload.blocks_read,
            expires_at=payload.expires_at,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "permission.scope_lock.created",
        {
            "scope_lock_id": lock.id,
            "resource_id": lock.resource_id,
            "locked_by": lock.locked_by,
            "reason": lock.reason,
            "blocks_read": lock.blocks_read,
        },
    )
    return lock


@app.delete("/scope-locks/{lock_id}", response_model=ScopeLockOut)
async def release_scope_lock(
    lock_id: int, payload: ScopeLockRelease, session: AsyncSession = Depends(get_session)
) -> ScopeLockOut:
    try:
        lock = await repository.release_scope_lock(session, lock_id, payload.released_by)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "permission.scope_lock.released",
        {
            "scope_lock_id": lock.id,
            "resource_id": lock.resource_id,
            "released_by": lock.released_by,
        },
    )
    return lock


@app.get("/scope-locks", response_model=list[ScopeLockOut])
async def list_scope_locks(
    resource_id: str | None = None, session: AsyncSession = Depends(get_session)
) -> list[ScopeLockOut]:
    return await repository.list_scope_locks(session, resource_id)


@app.get("/scope-locks/effective/{resource_id}", response_model=list[ScopeLockOut])
async def effective_scope_locks(
    resource_id: str, session: AsyncSession = Depends(get_session)
) -> list[ScopeLockOut]:
    return await repository.get_active_scope_locks_for_resource(session, resource_id)
