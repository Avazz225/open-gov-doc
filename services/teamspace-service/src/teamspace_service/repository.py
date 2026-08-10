"""Reine Datenbank-Operationen (2.5, P14-S6) - Cross-Service-Aufrufe
(`folder-service`/`permission-service`) orchestriert `main.py`, analog zum
durchgängigen Muster dieses Projekts (z. B. `workflow-service`,
`migration-service`): `repository.py` kennt keine HTTP-Clients."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from teamspace_service.models import (
    Teamspace,
    TeamspaceAppointment,
    TeamspaceContact,
    TeamspaceMember,
)


class NotFoundError(Exception):
    pass


class DuplicateMemberError(Exception):
    pass


async def create_teamspace(
    session: AsyncSession, *, name: str, description: str, root_folder_id: str, created_by: str
) -> Teamspace:
    """Legt den Teamspace UND die erste Mitgliedschaftszeile (die anlegende
    Person, `can_manage_members=True`) in einem Zug an - ein Teamspace ohne
    mindestens ein verwaltungsberechtigtes Mitglied wäre für niemanden mehr
    änderbar."""
    now = datetime.now(UTC)
    teamspace = Teamspace(
        name=name,
        description=description,
        root_folder_id=root_folder_id,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    session.add(teamspace)
    await session.flush()

    member = TeamspaceMember(
        teamspace_id=teamspace.id,
        principal_id=created_by,
        can_manage_members=True,
        invited_by=created_by,
        invited_at=now,
    )
    session.add(member)
    await session.flush()
    return teamspace


async def get_teamspace(session: AsyncSession, teamspace_id: str) -> Teamspace:
    teamspace = await session.get(Teamspace, teamspace_id)
    if teamspace is None:
        raise NotFoundError(f"teamspace_id {teamspace_id!r} unbekannt")
    return teamspace


async def list_teamspaces_for_principal(
    session: AsyncSession, principal_id: str
) -> list[Teamspace]:
    result = await session.execute(
        select(Teamspace)
        .join(TeamspaceMember, TeamspaceMember.teamspace_id == Teamspace.id)
        .where(TeamspaceMember.principal_id == principal_id)
        .order_by(Teamspace.name)
    )
    return list(result.scalars().all())


async def delete_teamspace(session: AsyncSession, teamspace_id: str) -> None:
    """Löscht nur die Teamspace-eigenen Metadaten (Zeile selbst, Mitglieder,
    Termine, Kontakte) - der `folder-service`-Wurzelordner samt Inhalt bleibt
    bewusst bestehen (verwaist, aber nicht automatisch gelöscht). Eine echte
    Löschung wäre ein eigenständiges, riskantes Feature (Aufbewahrungsfristen,
    Vier-Augen-Löschfreigabe, siehe 5.2) - für eine Referenzimplementierung
    bewusst nicht mitgebaut, siehe `docs/services/teamspace-service.md`
    "Bewusste Grenzen"."""
    teamspace = await get_teamspace(session, teamspace_id)
    await session.execute(
        TeamspaceMember.__table__.delete().where(TeamspaceMember.teamspace_id == teamspace_id)
    )
    await session.execute(
        TeamspaceAppointment.__table__.delete().where(
            TeamspaceAppointment.teamspace_id == teamspace_id
        )
    )
    await session.execute(
        TeamspaceContact.__table__.delete().where(TeamspaceContact.teamspace_id == teamspace_id)
    )
    await session.delete(teamspace)
    await session.flush()


async def get_member(
    session: AsyncSession, teamspace_id: str, principal_id: str
) -> TeamspaceMember | None:
    result = await session.execute(
        select(TeamspaceMember).where(
            TeamspaceMember.teamspace_id == teamspace_id,
            TeamspaceMember.principal_id == principal_id,
        )
    )
    return result.scalar_one_or_none()


async def list_members(session: AsyncSession, teamspace_id: str) -> list[TeamspaceMember]:
    result = await session.execute(
        select(TeamspaceMember)
        .where(TeamspaceMember.teamspace_id == teamspace_id)
        .order_by(TeamspaceMember.invited_at)
    )
    return list(result.scalars().all())


async def add_member(
    session: AsyncSession,
    teamspace_id: str,
    *,
    principal_id: str,
    can_manage_members: bool,
    invited_by: str,
) -> TeamspaceMember:
    existing = await get_member(session, teamspace_id, principal_id)
    if existing is not None:
        raise DuplicateMemberError(
            f"{principal_id!r} ist bereits Mitglied von Teamspace {teamspace_id!r}"
        )
    member = TeamspaceMember(
        teamspace_id=teamspace_id,
        principal_id=principal_id,
        can_manage_members=can_manage_members,
        invited_by=invited_by,
        invited_at=datetime.now(UTC),
    )
    session.add(member)
    await session.flush()
    return member


async def update_member(
    session: AsyncSession, teamspace_id: str, principal_id: str, *, can_manage_members: bool
) -> TeamspaceMember:
    member = await get_member(session, teamspace_id, principal_id)
    if member is None:
        raise NotFoundError(f"{principal_id!r} ist kein Mitglied von Teamspace {teamspace_id!r}")
    member.can_manage_members = can_manage_members
    await session.flush()
    return member


async def remove_member(session: AsyncSession, teamspace_id: str, principal_id: str) -> None:
    member = await get_member(session, teamspace_id, principal_id)
    if member is None:
        raise NotFoundError(f"{principal_id!r} ist kein Mitglied von Teamspace {teamspace_id!r}")
    await session.delete(member)
    await session.flush()


async def create_appointment(
    session: AsyncSession,
    teamspace_id: str,
    *,
    title: str,
    description: str,
    start_at: datetime,
    end_at: datetime,
    created_by: str,
) -> TeamspaceAppointment:
    appointment = TeamspaceAppointment(
        teamspace_id=teamspace_id,
        title=title,
        description=description,
        start_at=start_at,
        end_at=end_at,
        created_by=created_by,
        created_at=datetime.now(UTC),
    )
    session.add(appointment)
    await session.flush()
    return appointment


async def list_appointments(session: AsyncSession, teamspace_id: str) -> list[TeamspaceAppointment]:
    result = await session.execute(
        select(TeamspaceAppointment)
        .where(TeamspaceAppointment.teamspace_id == teamspace_id)
        .order_by(TeamspaceAppointment.start_at)
    )
    return list(result.scalars().all())


async def delete_appointment(session: AsyncSession, teamspace_id: str, appointment_id: int) -> None:
    appointment = await session.get(TeamspaceAppointment, appointment_id)
    if appointment is None or appointment.teamspace_id != teamspace_id:
        raise NotFoundError(f"appointment_id {appointment_id!r} unbekannt")
    await session.delete(appointment)
    await session.flush()


async def create_contact(
    session: AsyncSession,
    teamspace_id: str,
    *,
    name: str,
    email: str | None,
    phone: str | None,
    note: str,
    created_by: str,
) -> TeamspaceContact:
    contact = TeamspaceContact(
        teamspace_id=teamspace_id,
        name=name,
        email=email,
        phone=phone,
        note=note,
        created_by=created_by,
        created_at=datetime.now(UTC),
    )
    session.add(contact)
    await session.flush()
    return contact


async def list_contacts(session: AsyncSession, teamspace_id: str) -> list[TeamspaceContact]:
    result = await session.execute(
        select(TeamspaceContact)
        .where(TeamspaceContact.teamspace_id == teamspace_id)
        .order_by(TeamspaceContact.name)
    )
    return list(result.scalars().all())


async def delete_contact(session: AsyncSession, teamspace_id: str, contact_id: int) -> None:
    contact = await session.get(TeamspaceContact, contact_id)
    if contact is None or contact.teamspace_id != teamspace_id:
        raise NotFoundError(f"contact_id {contact_id!r} unbekannt")
    await session.delete(contact)
    await session.flush()
