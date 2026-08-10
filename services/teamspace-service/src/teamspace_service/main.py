import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from teamspace_service import repository
from teamspace_service.clients import FolderServiceClient, PermissionServiceClient
from teamspace_service.models import Base, TeamspaceMember
from teamspace_service.schemas import (
    TeamspaceAppointmentCreate,
    TeamspaceAppointmentOut,
    TeamspaceContactCreate,
    TeamspaceContactOut,
    TeamspaceCreate,
    TeamspaceMemberInvite,
    TeamspaceMemberOut,
    TeamspaceMemberUpdate,
    TeamspaceOut,
)
from teamspace_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS teamspace"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.folder_client = FolderServiceClient(settings.folder_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)

    event_bus = NatsEventBusClient(settings.nats_url, stream="teamspace")
    await event_bus.connect()
    app.state.event_bus = event_bus

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    if registration:
        await registration.stop()
    await event_bus.close()
    await app.state.folder_client.close()
    await app.state.permission_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


async def publish_event(
    event_type: str, subject: str, payload: dict, actor: str | None = None
) -> None:
    event = Event(
        event_type=event_type,
        service_name=settings.service_name,
        subject=subject,
        payload=payload,
        actor=actor,
    )
    await app.state.event_bus.publish(event_type, event.to_bytes())


async def _require_member(
    session: AsyncSession, teamspace_id: str, principal_id: str
) -> TeamspaceMember:
    """Das eigentliche, von der übrigen RBAC (4.1) unabhängige Zugriffsregime
    dieses Service (2.5, P14-S6) - eine reine Mitgliedschaftsprüfung gegen die
    eigene `teamspace_member`-Tabelle, kein Aufruf gegen `permission-service`."""
    if not principal_id:
        raise HTTPException(status_code=403, detail="X-DMS-Principal fehlt")
    member = await repository.get_member(session, teamspace_id, principal_id)
    if member is None:
        raise HTTPException(status_code=403, detail="Kein Mitglied dieses Teamspace")
    return member


async def _require_manager(
    session: AsyncSession, teamspace_id: str, principal_id: str
) -> TeamspaceMember:
    member = await _require_member(session, teamspace_id, principal_id)
    if not member.can_manage_members:
        raise HTTPException(status_code=403, detail="Verwaltung dieses Teamspace nicht erlaubt")
    return member


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.post("/teamspaces", response_model=TeamspaceOut, status_code=status.HTTP_201_CREATED)
async def create_teamspace(
    payload: TeamspaceCreate,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> TeamspaceOut:
    """Jeder authentifizierte Principal darf einen neuen Teamspace anlegen
    (Konzept 2.5: "ohne dass eine administrative Vorabeinrichtung nötig
    ist") - bewusst kein Capability-Gate wie bei Prozess-/DMN-Definitionen."""
    if not x_dms_principal:
        raise HTTPException(status_code=403, detail="X-DMS-Principal fehlt")
    folder = await app.state.folder_client.create_folder(
        name=payload.name, created_by=x_dms_principal
    )
    teamspace = await repository.create_teamspace(
        session,
        name=payload.name,
        description=payload.description,
        root_folder_id=folder["id"],
        created_by=x_dms_principal,
    )
    await app.state.permission_client.grant_resource_access(
        principal_id=x_dms_principal, resource_id=teamspace.root_folder_id
    )
    await session.commit()
    await publish_event(
        "teamspace.created",
        subject=teamspace.id,
        payload={"name": teamspace.name, "root_folder_id": teamspace.root_folder_id},
        actor=x_dms_principal,
    )
    return teamspace


@app.get("/teamspaces", response_model=list[TeamspaceOut])
async def list_teamspaces(
    x_dms_principal: str = Header(default=""), session: AsyncSession = Depends(get_session)
) -> list[TeamspaceOut]:
    if not x_dms_principal:
        raise HTTPException(status_code=403, detail="X-DMS-Principal fehlt")
    return await repository.list_teamspaces_for_principal(session, x_dms_principal)


@app.get("/teamspaces/{teamspace_id}", response_model=TeamspaceOut)
async def get_teamspace(
    teamspace_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> TeamspaceOut:
    try:
        teamspace = await repository.get_teamspace(session, teamspace_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_member(session, teamspace_id, x_dms_principal)
    return teamspace


@app.delete("/teamspaces/{teamspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_teamspace(
    teamspace_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Löscht nur die Teamspace-Metadaten, siehe
    `repository.delete_teamspace` - der Wurzelordner bleibt bestehen."""
    try:
        teamspace = await repository.get_teamspace(session, teamspace_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_manager(session, teamspace_id, x_dms_principal)
    members = await repository.list_members(session, teamspace_id)
    for member in members:
        await app.state.permission_client.revoke_resource_access(
            principal_id=member.principal_id, resource_id=teamspace.root_folder_id
        )
    await repository.delete_teamspace(session, teamspace_id)
    await session.commit()
    await publish_event(
        "teamspace.deleted", subject=teamspace_id, payload={}, actor=x_dms_principal
    )


@app.post(
    "/teamspaces/{teamspace_id}/members",
    response_model=TeamspaceMemberOut,
    status_code=status.HTTP_201_CREATED,
)
async def invite_member(
    teamspace_id: str,
    payload: TeamspaceMemberInvite,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> TeamspaceMemberOut:
    try:
        teamspace = await repository.get_teamspace(session, teamspace_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_manager(session, teamspace_id, x_dms_principal)
    try:
        member = await repository.add_member(
            session,
            teamspace_id,
            principal_id=payload.principal_id,
            can_manage_members=payload.can_manage_members,
            invited_by=x_dms_principal,
        )
    except repository.DuplicateMemberError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await app.state.permission_client.grant_resource_access(
        principal_id=payload.principal_id, resource_id=teamspace.root_folder_id
    )
    await session.commit()
    await publish_event(
        "teamspace.member_invited",
        subject=teamspace_id,
        payload={"principal_id": payload.principal_id},
        actor=x_dms_principal,
    )
    return member


@app.get("/teamspaces/{teamspace_id}/members", response_model=list[TeamspaceMemberOut])
async def list_members(
    teamspace_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[TeamspaceMemberOut]:
    await _require_member(session, teamspace_id, x_dms_principal)
    return await repository.list_members(session, teamspace_id)


@app.put("/teamspaces/{teamspace_id}/members/{principal_id}", response_model=TeamspaceMemberOut)
async def update_member(
    teamspace_id: str,
    principal_id: str,
    payload: TeamspaceMemberUpdate,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> TeamspaceMemberOut:
    await _require_manager(session, teamspace_id, x_dms_principal)
    try:
        member = await repository.update_member(
            session, teamspace_id, principal_id, can_manage_members=payload.can_manage_members
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return member


@app.delete(
    "/teamspaces/{teamspace_id}/members/{principal_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_member(
    teamspace_id: str,
    principal_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Die eigene Mitgliedschaft zu entfernen ("Teamspace verlassen") ist
    jedem Mitglied erlaubt; das Entfernen ANDERER Mitglieder verlangt
    `can_manage_members`. Bewusste Grenze: kein Schutz davor, dass sich das
    letzte verwaltungsberechtigte Mitglied selbst entfernt und der Teamspace
    dadurch unverwaltbar wird (siehe `docs/services/teamspace-service.md`)."""
    try:
        teamspace = await repository.get_teamspace(session, teamspace_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if principal_id == x_dms_principal:
        await _require_member(session, teamspace_id, x_dms_principal)
    else:
        await _require_manager(session, teamspace_id, x_dms_principal)
    try:
        await repository.remove_member(session, teamspace_id, principal_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await app.state.permission_client.revoke_resource_access(
        principal_id=principal_id, resource_id=teamspace.root_folder_id
    )
    await session.commit()
    await publish_event(
        "teamspace.member_removed",
        subject=teamspace_id,
        payload={"principal_id": principal_id},
        actor=x_dms_principal,
    )


@app.post(
    "/teamspaces/{teamspace_id}/appointments",
    response_model=TeamspaceAppointmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_appointment(
    teamspace_id: str,
    payload: TeamspaceAppointmentCreate,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> TeamspaceAppointmentOut:
    await _require_member(session, teamspace_id, x_dms_principal)
    appointment = await repository.create_appointment(
        session,
        teamspace_id,
        title=payload.title,
        description=payload.description,
        start_at=payload.start_at,
        end_at=payload.end_at,
        created_by=x_dms_principal,
    )
    await session.commit()
    return appointment


@app.get("/teamspaces/{teamspace_id}/appointments", response_model=list[TeamspaceAppointmentOut])
async def list_appointments(
    teamspace_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[TeamspaceAppointmentOut]:
    await _require_member(session, teamspace_id, x_dms_principal)
    return await repository.list_appointments(session, teamspace_id)


@app.delete(
    "/teamspaces/{teamspace_id}/appointments/{appointment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_appointment(
    teamspace_id: str,
    appointment_id: int,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Jedes Mitglied darf jeden Termin löschen - bewusst vollständig
    geteilt, kein Ersteller-exklusives Recht (siehe `models.py`)."""
    await _require_member(session, teamspace_id, x_dms_principal)
    try:
        await repository.delete_appointment(session, teamspace_id, appointment_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()


@app.post(
    "/teamspaces/{teamspace_id}/contacts",
    response_model=TeamspaceContactOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_contact(
    teamspace_id: str,
    payload: TeamspaceContactCreate,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> TeamspaceContactOut:
    await _require_member(session, teamspace_id, x_dms_principal)
    contact = await repository.create_contact(
        session,
        teamspace_id,
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        note=payload.note,
        created_by=x_dms_principal,
    )
    await session.commit()
    return contact


@app.get("/teamspaces/{teamspace_id}/contacts", response_model=list[TeamspaceContactOut])
async def list_contacts(
    teamspace_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[TeamspaceContactOut]:
    await _require_member(session, teamspace_id, x_dms_principal)
    return await repository.list_contacts(session, teamspace_id)


@app.delete(
    "/teamspaces/{teamspace_id}/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_contact(
    teamspace_id: str,
    contact_id: int,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _require_member(session, teamspace_id, x_dms_principal)
    try:
        await repository.delete_contact(session, teamspace_id, contact_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
