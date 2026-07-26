from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from permission_service.models import EffectivePermissionCache, ResourceNode, Role, RoleAssignment
from permission_service.settings import ROOT_RESOURCE_ID


class NotFoundError(Exception):
    pass


async def invalidate_cache(session: AsyncSession) -> None:
    """Leert den gesamten materialisierten Cache (siehe Docstring an
    ``EffectivePermissionCache`` für die Begründung der Grobkörnigkeit)."""
    await session.execute(delete(EffectivePermissionCache))


async def ensure_root_resource(session: AsyncSession) -> None:
    existing = await session.get(ResourceNode, ROOT_RESOURCE_ID)
    if existing is None:
        session.add(
            ResourceNode(resource_id=ROOT_RESOURCE_ID, parent_id=None, resource_type="root")
        )
        await session.flush()


async def create_role(
    session: AsyncSession, name: str, description: str, permissions: list[str]
) -> Role:
    role = Role(name=name, description=description, permissions=permissions)
    session.add(role)
    await session.flush()
    return role


async def list_roles(session: AsyncSession) -> list[Role]:
    result = await session.execute(select(Role))
    return list(result.scalars().all())


async def create_role_assignment(
    session: AsyncSession, *, principal_type: str, principal_id: str, role_id: int, resource_id: str
) -> RoleAssignment:
    resource = await session.get(ResourceNode, resource_id)
    if resource is None:
        raise NotFoundError(f"resource_id {resource_id!r} unbekannt")
    role = await session.get(Role, role_id)
    if role is None:
        raise NotFoundError(f"role_id {role_id!r} unbekannt")

    assignment = RoleAssignment(
        principal_type=principal_type,
        principal_id=principal_id,
        role_id=role_id,
        resource_id=resource_id,
    )
    session.add(assignment)
    await session.flush()
    await invalidate_cache(session)
    return assignment


async def delete_role_assignment(session: AsyncSession, assignment_id: int) -> None:
    assignment = await session.get(RoleAssignment, assignment_id)
    if assignment is None:
        raise NotFoundError(f"role_assignment {assignment_id!r} unbekannt")
    await session.delete(assignment)
    await invalidate_cache(session)


async def set_resource_inherit(
    session: AsyncSession, resource_id: str, inherit: bool
) -> ResourceNode:
    resource = await session.get(ResourceNode, resource_id)
    if resource is None:
        raise NotFoundError(f"resource_id {resource_id!r} unbekannt")
    resource.inherit = inherit
    await session.flush()
    await invalidate_cache(session)
    return resource


async def _collect_effective_roles(
    session: AsyncSession, principal_id: str, resource_id: str
) -> list[Role]:
    """Läuft die Vorfahrenkette von ``resource_id`` nach oben und sammelt alle
    Rollen, die dem Principal an jedem durchlaufenen Knoten zugewiesen sind.
    Ein Knoten mit ``inherit=False`` beendet den Aufstieg NACH Auswertung
    seiner eigenen Zuweisungen (4.1: Vererbung mit Override-Möglichkeit,
    Standard-DMS-Verhalten wie SharePoint/Alfresco).
    """
    collected: dict[int, Role] = {}
    current_id: str | None = resource_id

    while current_id is not None:
        node = await session.get(ResourceNode, current_id)
        if node is None:
            break

        result = await session.execute(
            select(RoleAssignment).where(
                RoleAssignment.resource_id == current_id,
                RoleAssignment.principal_id == principal_id,
            )
        )
        for assignment in result.scalars().all():
            role = await session.get(Role, assignment.role_id)
            if role is not None:
                collected[role.id] = role

        if not node.inherit:
            break
        current_id = node.parent_id

    return list(collected.values())


async def get_effective_permissions(
    session: AsyncSession, principal_id: str, resource_id: str
) -> EffectivePermissionCache:
    cached = await session.get(EffectivePermissionCache, (principal_id, resource_id))
    if cached is not None:
        return cached

    roles = await _collect_effective_roles(session, principal_id, resource_id)
    permissions = sorted({p for role in roles for p in role.permissions})
    entry = EffectivePermissionCache(
        principal_id=principal_id,
        resource_id=resource_id,
        roles=sorted(role.name for role in roles),
        permissions=permissions,
        computed_at=datetime.now(UTC),
    )
    await session.merge(entry)
    await session.flush()
    return entry
