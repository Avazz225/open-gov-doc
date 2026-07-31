import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from permission_service.models import (
    ApprovalActionConfig,
    ApprovalRequest,
    EffectivePermissionCache,
    ResourceNode,
    Role,
    RoleAssignment,
    ScopeLock,
)
from permission_service.settings import ROOT_RESOURCE_ID


class NotFoundError(Exception):
    pass


class ApprovalRequestNotPendingError(Exception):
    """Der Request wurde bereits genehmigt/abgelehnt - keine zweite Entscheidung möglich."""


class NotInitiatorAllowedError(Exception):
    """Vier-Augen-Kernregel (4.3): die genehmigende Person darf nicht mit der
    initiierenden Person identisch sein."""


class MissingRequiredPermissionError(Exception):
    """Verschärfung für 4.6 (Break-Glass): der Aktionstyp verlangt laut
    ``ApprovalActionConfig.required_permission`` eine bestimmte Capability an
    der Wurzelressource - weder Initiator noch Genehmiger sind davon
    ausgenommen."""


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


async def get_role_by_name(session: AsyncSession, name: str) -> Role | None:
    result = await session.execute(select(Role).where(Role.name == name))
    return result.scalars().first()


# Domänengetrennte Admin-Rollen (4.6) - systemeigen (in diesem Service, NICHT
# als Keycloak-Realm-Rollen), damit sie unabhängig vom Identity Provider
# funktionieren. Nur "domain-admin-users" bekommt diese Session ein
# zugeordnetes technisches Keycloak-Konto + tatsächliche Durchsetzung (siehe
# auth-service); die übrigen sechs Domänen sind laut 4.6 "standardmäßig
# mitgeliefert", aber bewusst noch ohne Konto/Enforcement (deren fachliche
# Funktionen existieren teils noch gar nicht, z. B. Lizenzverwaltung/
# Query-Konsole). "breakglass-approver" ist keine Domäne aus 4.6, sondern die
# Gruppen-Rolle für die Vier-Augen-Freigabe der Superuser-Aktivierung.
DOMAIN_ADMIN_ROLES: list[tuple[str, str, list[str]]] = [
    ("domain-admin-users", "Nutzer-/Rechteverwaltung", ["admin.user_management"]),
    ("domain-admin-config", "Objekttyp-/Workflow-Konfiguration", ["admin.object_config"]),
    ("domain-admin-storage", "Storage-/Backend-Verwaltung", ["admin.storage"]),
    ("domain-admin-license", "Lizenzverwaltung", ["admin.license"]),
    ("domain-admin-query-console", "Query-Konsole", ["admin.query_console"]),
    ("domain-admin-deletion", "Löschadministration", ["admin.deletion"]),
    (
        "domain-admin-deletion-vs",
        "Löschadministration (Verschlusssachen)",
        ["admin.deletion_classified"],
    ),
    ("breakglass-approver", "Freigabegruppe Superuser-Break-Glass (4.6)", ["breakglass.approve"]),
]


async def ensure_domain_admin_roles(session: AsyncSession) -> None:
    for name, description, permissions in DOMAIN_ADMIN_ROLES:
        if await get_role_by_name(session, name) is None:
            await create_role(session, name, description, permissions)


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


async def list_role_assignments(
    session: AsyncSession, *, principal_id: str | None = None, resource_id: str | None = None
) -> list[RoleAssignment]:
    """Grundlage der Admin-UI-Nutzer-/Rollenverwaltung (P4-S3) - bisher gab es
    nur Erstellen/Löschen einzelner Zuweisungen, kein Auflisten."""
    query = select(RoleAssignment)
    if principal_id is not None:
        query = query.where(RoleAssignment.principal_id == principal_id)
    if resource_id is not None:
        query = query.where(RoleAssignment.resource_id == resource_id)
    result = await session.execute(query)
    return list(result.scalars().all())


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


async def create_scope_lock(
    session: AsyncSession,
    *,
    resource_id: str,
    locked_by: str,
    reason: str | None,
    blocks_read: bool,
    expires_at: datetime | None,
) -> ScopeLock:
    resource = await session.get(ResourceNode, resource_id)
    if resource is None:
        raise NotFoundError(f"resource_id {resource_id!r} unbekannt")

    lock = ScopeLock(
        resource_id=resource_id,
        locked_by=locked_by,
        reason=reason,
        blocks_read=blocks_read,
        expires_at=expires_at,
        created_at=datetime.now(UTC),
    )
    session.add(lock)
    await session.flush()
    return lock


async def release_scope_lock(session: AsyncSession, lock_id: int, released_by: str) -> ScopeLock:
    lock = await session.get(ScopeLock, lock_id)
    if lock is None or lock.released_at is not None:
        raise NotFoundError(f"aktive scope_lock {lock_id!r} unbekannt")
    lock.released_at = datetime.now(UTC)
    lock.released_by = released_by
    await session.flush()
    return lock


def _scope_lock_is_active(lock: ScopeLock, now: datetime) -> bool:
    if lock.released_at is not None:
        return False
    return lock.expires_at is None or lock.expires_at > now


async def list_scope_locks(session: AsyncSession, resource_id: str | None) -> list[ScopeLock]:
    stmt = select(ScopeLock)
    if resource_id is not None:
        stmt = stmt.where(ScopeLock.resource_id == resource_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_active_scope_locks_for_resource(
    session: AsyncSession, resource_id: str
) -> list[ScopeLock]:
    """Läuft die Vorfahrenkette von ``resource_id`` nach oben (wie
    ``_collect_effective_roles``) und sammelt alle aktiven Bereichssperren
    (4.7) - unabhängig vom ``inherit``-Flag, das nur die RBAC-Vererbung
    betrifft: eine Bereichssperre gilt immer für den gesamten Unterbaum."""
    now = datetime.now(UTC)
    active: list[ScopeLock] = []
    current_id: str | None = resource_id

    while current_id is not None:
        node = await session.get(ResourceNode, current_id)
        if node is None:
            break

        result = await session.execute(select(ScopeLock).where(ScopeLock.resource_id == current_id))
        for lock in result.scalars().all():
            if _scope_lock_is_active(lock, now):
                active.append(lock)

        current_id = node.parent_id

    return active


async def get_approval_config(session: AsyncSession, action_type: str) -> ApprovalActionConfig:
    """Liest die Vier-Augen-Konfiguration für einen Aktionstyp (4.3). Fehlt
    die Zeile, wird ein transientes (nicht persistiertes) Default-Objekt mit
    ``requires_approval=False`` zurückgegeben - "konfigurierbar pro
    Aktionstyp, nicht global erzwungen" heißt: ohne explizite Aktivierung
    bleibt jede Aktion ungated."""
    config = await session.get(ApprovalActionConfig, action_type)
    if config is None:
        return ApprovalActionConfig(
            action_type=action_type, requires_approval=False, updated_at=datetime.now(UTC)
        )
    return config


async def list_approval_configs(session: AsyncSession) -> list[ApprovalActionConfig]:
    result = await session.execute(select(ApprovalActionConfig))
    return list(result.scalars().all())


async def set_approval_config(
    session: AsyncSession,
    action_type: str,
    *,
    requires_approval: bool,
    required_permission: str | None = None,
) -> ApprovalActionConfig:
    config = await session.get(ApprovalActionConfig, action_type)
    if config is None:
        config = ApprovalActionConfig(
            action_type=action_type,
            requires_approval=requires_approval,
            required_permission=required_permission,
            updated_at=datetime.now(UTC),
        )
        session.add(config)
    else:
        config.requires_approval = requires_approval
        config.required_permission = required_permission
        config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


async def _require_permission_if_configured(
    session: AsyncSession, config: ApprovalActionConfig, principal_id: str
) -> None:
    if config.required_permission is None:
        return
    entry = await get_effective_permissions(session, principal_id, ROOT_RESOURCE_ID)
    if config.required_permission not in entry.permissions:
        raise MissingRequiredPermissionError(
            f"{principal_id!r} hält nicht die für {config.action_type!r} "
            f"erforderliche Capability {config.required_permission!r}"
        )


async def create_approval_request(
    session: AsyncSession, *, action_type: str, initiated_by: str, payload: dict
) -> ApprovalRequest:
    config = await get_approval_config(session, action_type)
    await _require_permission_if_configured(session, config, initiated_by)
    request = ApprovalRequest(
        id=str(uuid.uuid4()),
        action_type=action_type,
        initiated_by=initiated_by,
        payload=payload,
        status="pending",
        created_at=datetime.now(UTC),
    )
    session.add(request)
    await session.flush()
    return request


async def get_approval_request(session: AsyncSession, request_id: str) -> ApprovalRequest:
    request = await session.get(ApprovalRequest, request_id)
    if request is None:
        raise NotFoundError(f"approval_request {request_id!r} unbekannt")
    return request


async def list_approval_requests(
    session: AsyncSession, *, status: str | None = None, action_type: str | None = None
) -> list[ApprovalRequest]:
    stmt = select(ApprovalRequest)
    if status is not None:
        stmt = stmt.where(ApprovalRequest.status == status)
    if action_type is not None:
        stmt = stmt.where(ApprovalRequest.action_type == action_type)
    result = await session.execute(stmt.order_by(ApprovalRequest.created_at))
    return list(result.scalars().all())


async def approve_request(
    session: AsyncSession, request_id: str, *, approved_by: str
) -> ApprovalRequest:
    request = await get_approval_request(session, request_id)
    if request.status != "pending":
        raise ApprovalRequestNotPendingError(
            f"approval_request {request_id!r} ist bereits {request.status!r}"
        )
    if approved_by == request.initiated_by:
        raise NotInitiatorAllowedError(
            "Genehmigende Person darf nicht mit der initiierenden Person identisch sein"
        )
    config = await get_approval_config(session, request.action_type)
    await _require_permission_if_configured(session, config, approved_by)
    request.status = "approved"
    request.approved_by = approved_by
    request.decided_at = datetime.now(UTC)
    await session.flush()
    return request


async def reject_request(
    session: AsyncSession, request_id: str, *, rejected_by: str, reason: str | None
) -> ApprovalRequest:
    request = await get_approval_request(session, request_id)
    if request.status != "pending":
        raise ApprovalRequestNotPendingError(
            f"approval_request {request_id!r} ist bereits {request.status!r}"
        )
    request.status = "rejected"
    request.rejected_by = rejected_by
    request.reason = reason
    request.decided_at = datetime.now(UTC)
    await session.flush()
    return request
