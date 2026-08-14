"""Pure database operations (2.5, P14-S6) - cross-service calls
(`folder-service`/`permission-service`) are orchestrated by `main.py`,
analogous to this project's consistent pattern (e.g. `workflow-service`,
`migration-service`): `repository.py` knows nothing about HTTP clients."""

from datetime import UTC, datetime

from sqlalchemy import func, select
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
    """Creates the teamspace AND the first membership row (the creating
    person, `can_manage_members=True`) in one go - a teamspace without at
    least one member with management rights would no longer be
    changeable by anyone."""
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


async def list_all_teamspaces_with_member_counts(
    session: AsyncSession,
) -> list[tuple[Teamspace, int]]:
    """Installation-wide overview (Post-Roadmap Phase 22 Session 5) -
    unlike `list_teamspaces_for_principal`, NOT filtered by membership,
    hence gated at the `main.py` level via `admin.teamspace_management`
    instead of via the service's own `teamspace_member` table (as the
    rest of this service's endpoints are). `outerjoin` so that, while a
    teamspace could in principle never appear without a member (see
    `create_teamspace`), the count would still not silently omit the
    entire row if the member list were ever empty in the future."""
    result = await session.execute(
        select(Teamspace, func.count(TeamspaceMember.principal_id))
        .outerjoin(TeamspaceMember, TeamspaceMember.teamspace_id == Teamspace.id)
        .group_by(Teamspace.id)
        .order_by(Teamspace.name)
    )
    return [(row[0], row[1]) for row in result.all()]


async def delete_teamspace(session: AsyncSession, teamspace_id: str) -> None:
    """Deletes only the teamspace's own metadata (the row itself, members,
    appointments, contacts) - the `folder-service` root folder along with
    its content deliberately remains (orphaned, but not automatically
    deleted). A real deletion would be a separate, risky feature
    (retention periods, four-eyes deletion approval, see 5.2) -
    deliberately not built for a reference implementation, see
    `docs/services/teamspace-service.md` "Deliberate boundaries"."""
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
