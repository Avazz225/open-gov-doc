import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from permission_service.models import (
    ApprovalActionConfig,
    ApprovalRequest,
    Delegation,
    EffectivePermissionCache,
    Group,
    GroupMembership,
    ResourceNode,
    Role,
    RoleAssignment,
    ScopeLock,
    SystemMaintenanceMode,
)
from permission_service.settings import ROOT_RESOURCE_ID


class NotFoundError(Exception):
    pass


class ApprovalRequestNotPendingError(Exception):
    """The request has already been approved/rejected - a second decision is not possible."""


class NotInitiatorAllowedError(Exception):
    """Core four-eyes rule (4.3): the approving person must not be identical
    to the initiating person."""


class MissingRequiredPermissionError(Exception):
    """Tightening for 4.6 (break-glass): the action type requires a specific
    capability at the root resource, as stated by
    ``ApprovalActionConfig.required_permission`` - neither the initiator nor
    the approver is exempt from this."""


async def invalidate_cache(session: AsyncSession) -> None:
    """Clears the entire materialized cache (see the docstring on
    ``EffectivePermissionCache`` for the rationale behind the coarse
    granularity)."""
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


async def update_role(
    session: AsyncSession, role_id: int, *, description: str, permissions: list[str]
) -> Role:
    """Invalidates the effective-permissions cache since Phase 19 Session 3
    (ADR 0068) - previously missing here (unlike every other permission-
    changing operation in this module), which was not a problem as long as
    roles were edited only rarely and without an acute security expectation.
    With the "everyone" group (P19-S2/S3), an admin might now deliberately
    edit this role to immediately revoke a permission from an already
    cached principal - without invalidation, that would have had no effect
    until the next, independent cache clear (e.g. from another role
    assignment)."""
    role = await session.get(Role, role_id)
    if role is None:
        raise NotFoundError(f"role_id {role_id!r} unbekannt")
    role.description = description
    role.permissions = permissions
    await session.flush()
    await invalidate_cache(session)
    return role


async def get_role_by_name(session: AsyncSession, name: str) -> Role | None:
    result = await session.execute(select(Role).where(Role.name == name))
    return result.scalars().first()


# Domain-separated admin roles (4.6) - native to this service (NOT as
# Keycloak realm roles), so they work independently of the identity
# provider. "domain-admin-users" was the first to get an associated
# technical Keycloak account + actual enforcement (see auth-service).
# "domain-admin-query-console"/"-manipulate" got their actual enforcement in
# query-service (P8-S1/P8-S2, no own technical account needed - a plain
# role assignment suffices). The remaining domains are, per 4.6, "shipped by
# default" but deliberately still without enforcement (their functional
# capabilities partly don't exist yet, e.g. license management).
# "breakglass-approver" is not a domain from 4.6, but the group role for the
# four-eyes approval of superuser activation.
DOMAIN_ADMIN_ROLES: list[tuple[str, str, list[str]]] = [
    ("domain-admin-users", "Nutzer-/Rechteverwaltung", ["admin.user_management"]),
    ("domain-admin-config", "Objekttyp-/Workflow-Konfiguration", ["admin.object_config"]),
    ("domain-admin-storage", "Storage-/Backend-Verwaltung", ["admin.storage"]),
    ("domain-admin-license", "Lizenzverwaltung", ["admin.license"]),
    ("domain-admin-query-console", "Query-Konsole", ["admin.query_console"]),
    (
        "domain-admin-query-console-manipulate",
        "Query-Konsole (Manipulation)",
        ["admin.query_console.manipulate"],
    ),
    ("domain-admin-deletion", "Löschadministration", ["admin.deletion"]),
    (
        "domain-admin-deletion-vs",
        "Löschadministration (Verschlusssachen)",
        ["admin.deletion_classified"],
    ),
    ("breakglass-approver", "Freigabegruppe Superuser-Break-Glass (4.6)", ["breakglass.approve"]),
    # Also not a concept-4.6 domain entry, but the trigger role for the
    # emergency lock (4.8, P6-S6) - deliberately without an automatic
    # technical account (like breakglass-approver, unlike users-admin/
    # config-admin): an emergency lock should remain attributed to a real,
    # individually accountable person, not a shared account.
    ("domain-admin-emergency", "Not-Shutdown-Auslösung (4.8)", ["system.not_shutdown.trigger"]),
    # Like "domain-admin-license" (P9-S1), this domain only comes into being
    # with the actual feature (Plugin Orchestration Service, 3.8, P10-S1) -
    # concept 4.6 explicitly calls its domain list only exemplary.
    ("domain-admin-orchestration", "Plugin-Orchestrierung", ["admin.orchestration"]),
    # Like "domain-admin-orchestration" (P10-S1), this domain only comes into
    # being with the actual feature (sensor concept/monitoring-service, 10.1,
    # P11-S1) - deactivating security-relevant sensors is, per the concept,
    # itself a security-relevant, audited operation.
    ("domain-admin-monitoring", "Monitoring-/Sensor-Konfiguration", ["admin.monitoring"]),
    # Post-Roadmap Phase 19 Session 8 (ADR 0073): replaces virus-scan-
    # service's previous plain `X-DMS-Roles` gate (`quarantine_admin_role`,
    # "dms-admin") - "a dedicated, narrowly scoped role may view a
    # quarantine case, permanently delete it, or ... release it" (concept
    # 2.5, verbatim) is now a real, admin-editable domain instead of a
    # hard-coded Keycloak realm role name.
    ("domain-admin-virus-scan", "Virenschutz-/Quarantäne-Verwaltung", ["admin.quarantine"]),
    # Post-Roadmap Phase 19 Session 10 (ADR 0075): concept 5.2 does not name
    # a dedicated role for legal hold - a separate domain instead of reusing
    # "domain-admin-deletion" (records disposal administration), since the
    # two are conceptually opposite (a hold prevents deletion, deletion
    # admin performs it) - see ADR 0075 "Rationale".
    ("domain-admin-legal-hold", "Legal-Hold-Verwaltung", ["admin.legal_hold"]),
    # Post-Roadmap Phase 22 Session 5: `teamspace-service`'s new
    # installation-wide overview endpoint (`GET /admin/teamspaces`, shows
    # ALL teamspaces, not just those of the requesting principal like
    # `GET /teamspaces`) is the first place in this service that needs a
    # real permission check - teamspaces themselves remain self-managed
    # (2.5, no capability gate for creation/joining).
    ("domain-admin-teamspaces", "Teamspace-Aufsicht", ["admin.teamspace_management"]),
]


async def ensure_domain_admin_roles(session: AsyncSession) -> None:
    for name, description, permissions in DOMAIN_ADMIN_ROLES:
        if await get_role_by_name(session, name) is None:
            await create_role(session, name, description, permissions)


# "everyone" group (Post-Roadmap Phase 19 Session 2, ADR 0067): every
# authenticated principal is implicitly a member, without any account
# needing to be assigned individually - `_collect_effective_roles` below
# treats a `RoleAssignment` with exactly this `(principal_type,
# principal_id)` pair at every traversed resource node as applying to EVERY
# principal. Replaces the previously hard-coded "any authenticated user
# may..." bypasses in `auth-service` (`GET /users/lookup`, `GET
# /users/directory`, see P19-S3) with a real, admin-editable role - the
# switch itself changes nothing about actual behavior, only enforcement
# becomes native instead of hard-wired.
EVERYONE_PRINCIPAL_TYPE = "group"
EVERYONE_PRINCIPAL_ID = "everyone"
EVERYONE_ROLE_NAME = "everyone"
EVERYONE_ROLE_DESCRIPTION = (
    "Jeder authentifizierte Principal (implizite Mitgliedschaft, keine Zuweisung pro Konto nötig)"
)
# Since Phase 19 Session 5 (ADR 0070) additionally `case.read`/`case.write` -
# case-service previously had NO permission check whatsoever; this extension
# preserves the previous de-facto-open behavior but makes it admin-editable
# instead of hard-coded (same principle as users.lookup/directory in
# P19-S3). IMPORTANT: `ensure_everyone_role` below does not automatically
# update an ALREADY created role (no Alembic-like migration mechanism in
# this project, see `ensure_domain_admin_roles` with the same limitation) -
# on an already-running installation, this extension must be applied once
# manually via `PUT /roles/{id}` (as a real admin would do), otherwise it
# only takes effect for instances that don't yet have "everyone".
# Deliberately NOT self-healing: otherwise an admin's targeted revocation
# (e.g. ADR 0068's users.lookup example) could get unintentionally undone on
# every restart.
EVERYONE_ROLE_PERMISSIONS: list[str] = [
    "users.lookup",
    "users.directory",
    "case.read",
    "case.write",
    # Post-Roadmap Phase 19 Session 7 (ADR 0072): archival-service/
    # reporting-service previously had NO RBAC check whatsoever.
    "archival.read",
    "archival.write",
    "reporting.read",
    "reporting.write",
    "reporting.forensic_trace",
    # Post-Roadmap Phase 19 Session 8 (ADR 0073): ocr-service/rendering-
    # service previously had NO RBAC check whatsoever. `admin.quarantine`
    # (virus-scan-service, same session) is deliberately NOT listed here -
    # unlike these two, the quarantine area was already a real permission
    # restricted to a dedicated role before, not a previously de-facto-open
    # gap.
    "ocr.read",
    "ocr.write",
    "rendering.read",
    "rendering.write",
    # Post-Roadmap Phase 19 Session 9 (ADR 0074): starting workflow
    # instances/completing tasks were previously a deliberate, hard-coded
    # "open to every authenticated principal" decision (P6-S6) - "everyone"
    # preserves this behavior but makes it admin-editable.
    "workflow.write",
]


async def ensure_everyone_role(session: AsyncSession) -> None:
    """Idempotent (same pattern as `ensure_domain_admin_roles`) - in addition
    to the role itself, also creates its `RoleAssignment` at the root
    resource, since the "everyone" group (unlike domain-admin roles) has no
    external account the assignment could otherwise be attributed to. Runs
    AFTER `ensure_root_resource` (needs `ROOT_RESOURCE_ID` as the FK target)
    and deliberately acts directly against the session instead of through
    the four-eyes-gated `POST /role-assignments` endpoint - this is
    bootstrap infrastructure like `ensure_domain_admin_roles`, not a
    runtime admin action."""
    role = await get_role_by_name(session, EVERYONE_ROLE_NAME)
    if role is None:
        role = await create_role(
            session, EVERYONE_ROLE_NAME, EVERYONE_ROLE_DESCRIPTION, EVERYONE_ROLE_PERMISSIONS
        )

    existing_assignments = await list_role_assignments(
        session, principal_id=EVERYONE_PRINCIPAL_ID, resource_id=ROOT_RESOURCE_ID
    )
    if not any(a.role_id == role.id for a in existing_assignments):
        await create_role_assignment(
            session,
            principal_type=EVERYONE_PRINCIPAL_TYPE,
            principal_id=EVERYONE_PRINCIPAL_ID,
            role_id=role.id,
            resource_id=ROOT_RESOURCE_ID,
        )


# Admin-creatable groups (Post-Roadmap Phase 22 Session 2) - complement the
# hard-coded "everyone" group above with real, admin-managed groups. Same
# gating as roles themselves (``_require_role_management`` in ``main.py``,
# `admin.user_management`), since a group is ultimately just another
# building block of permission management.
async def create_group(session: AsyncSession, name: str, description: str) -> Group:
    group = Group(
        id=str(uuid.uuid4()), name=name, description=description, created_at=datetime.now(UTC)
    )
    session.add(group)
    await session.flush()
    return group


async def list_groups(session: AsyncSession) -> list[Group]:
    result = await session.execute(select(Group))
    return list(result.scalars().all())


async def delete_group(session: AsyncSession, group_id: str) -> None:
    """Deletes the group along with its memberships. Deliberately NO check
    for still-existing `RoleAssignment` rows pointing at this group
    (``principal_id=group_id``) - such a row simply no longer matches any
    principal afterward (empty member list), the same behavior as a group
    that never had any members assigned. Consistent with `Role`, which
    likewise has no delete endpoint/reference check."""
    group = await session.get(Group, group_id)
    if group is None:
        raise NotFoundError(f"group_id {group_id!r} unbekannt")
    await session.execute(delete(GroupMembership).where(GroupMembership.group_id == group_id))
    await session.delete(group)
    await invalidate_cache(session)


async def add_group_member(
    session: AsyncSession, group_id: str, principal_id: str
) -> GroupMembership:
    group = await session.get(Group, group_id)
    if group is None:
        raise NotFoundError(f"group_id {group_id!r} unbekannt")
    existing = await session.execute(
        select(GroupMembership).where(
            GroupMembership.group_id == group_id, GroupMembership.principal_id == principal_id
        )
    )
    membership = existing.scalars().first()
    if membership is not None:
        return membership
    membership = GroupMembership(group_id=group_id, principal_id=principal_id)
    session.add(membership)
    await session.flush()
    await invalidate_cache(session)
    return membership


async def list_group_members(session: AsyncSession, group_id: str) -> list[GroupMembership]:
    result = await session.execute(
        select(GroupMembership).where(GroupMembership.group_id == group_id)
    )
    return list(result.scalars().all())


async def remove_group_member(session: AsyncSession, group_id: str, principal_id: str) -> None:
    result = await session.execute(
        select(GroupMembership).where(
            GroupMembership.group_id == group_id, GroupMembership.principal_id == principal_id
        )
    )
    membership = result.scalars().first()
    if membership is None:
        raise NotFoundError(f"{principal_id!r} ist kein Mitglied von Gruppe {group_id!r}")
    await session.delete(membership)
    await invalidate_cache(session)


async def _group_ids_for_principal(session: AsyncSession, principal_id: str) -> set[str]:
    result = await session.execute(
        select(GroupMembership.group_id).where(GroupMembership.principal_id == principal_id)
    )
    return set(result.scalars().all())


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
    """Basis for the admin UI's user/role management (P4-S3) - previously
    there was only creation/deletion of individual assignments, no listing."""
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
    """Walks the ancestor chain of ``resource_id`` upward and collects all
    roles assigned to the principal at every traversed node. A node with
    ``inherit=False`` stops the ascent AFTER evaluating its own assignments
    (4.1: inheritance with override capability, standard DMS behavior like
    SharePoint/Alfresco).

    Since Phase 19 Session 2, also includes assignments to the "everyone"
    group (``principal_type="group", principal_id="everyone"``, see
    ``ensure_everyone_role``) - every authenticated principal implicitly
    counts as a member for this purpose, regardless of their own
    `principal_id`. Since Post-Roadmap Phase 22 Session 2, also includes
    assignments to any real, admin-created group (``Group``/
    ``GroupMembership``) that the principal belongs to via explicit
    membership - unlike "everyone", this requires an actual row.
    """
    collected: dict[int, Role] = {}
    current_id: str | None = resource_id
    member_group_ids = await _group_ids_for_principal(session, principal_id)

    while current_id is not None:
        node = await session.get(ResourceNode, current_id)
        if node is None:
            break

        group_conditions = [
            and_(
                RoleAssignment.principal_type == EVERYONE_PRINCIPAL_TYPE,
                RoleAssignment.principal_id == EVERYONE_PRINCIPAL_ID,
            )
        ]
        if member_group_ids:
            group_conditions.append(
                and_(
                    RoleAssignment.principal_type == "group",
                    RoleAssignment.principal_id.in_(member_group_ids),
                )
            )

        result = await session.execute(
            select(RoleAssignment).where(
                RoleAssignment.resource_id == current_id,
                or_(RoleAssignment.principal_id == principal_id, *group_conditions),
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
    """Walks the ancestor chain of ``resource_id`` upward (like
    ``_collect_effective_roles``) and collects all active scope locks (4.7) -
    independent of the ``inherit`` flag, which only affects RBAC
    inheritance: a scope lock always applies to the entire subtree."""
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
    """Reads the four-eyes configuration for an action type (4.3). If the
    row is missing, a transient (non-persisted) default object with
    ``requires_approval=False`` is returned - "configurable per action type,
    not globally enforced" means: without explicit activation, every action
    stays ungated."""
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


async def require_capability(session: AsyncSession, principal_id: str, permission: str) -> None:
    """Direct baseline permission check at the root resource - unlike
    `_require_permission_if_configured` below (which only applies *in the
    context of an approval request*), also usable for endpoints without
    any four-eyes involvement (e.g. `POST /maintenance-mode/trigger`, when
    `requires_approval=False`, 4.8/P6-S6)."""
    entry = await get_effective_permissions(session, principal_id, ROOT_RESOURCE_ID)
    if permission not in entry.permissions:
        raise MissingRequiredPermissionError(
            f"{principal_id!r} hält nicht die erforderliche Capability {permission!r}"
        )


async def _require_permission_if_configured(
    session: AsyncSession, config: ApprovalActionConfig, principal_id: str
) -> None:
    if config.required_permission is None:
        return
    await require_capability(session, principal_id, config.required_permission)


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


MAINTENANCE_MODE_ID = 1


async def get_or_seed_maintenance_mode(session: AsyncSession) -> SystemMaintenanceMode:
    """Singleton row (4.8, P6-S6) - created on first access instead of being
    unconditionally required at service start, same pattern as
    `get_approval_config`'s transient default object, only actually
    persisted here (the status must survive restarts)."""
    mode = await session.get(SystemMaintenanceMode, MAINTENANCE_MODE_ID)
    if mode is None:
        mode = SystemMaintenanceMode(id=MAINTENANCE_MODE_ID, active=False)
        session.add(mode)
        await session.flush()
    return mode


async def activate_maintenance_mode(
    session: AsyncSession, *, triggered_by: str, reason: str | None
) -> SystemMaintenanceMode:
    mode = await get_or_seed_maintenance_mode(session)
    mode.active = True
    mode.reason = reason
    mode.triggered_by = triggered_by
    mode.activated_at = datetime.now(UTC)
    mode.lifted_by = None
    mode.lifted_at = None
    await session.flush()
    return mode


async def lift_maintenance_mode(session: AsyncSession, *, lifted_by: str) -> SystemMaintenanceMode:
    mode = await get_or_seed_maintenance_mode(session)
    mode.active = False
    mode.lifted_by = lifted_by
    mode.lifted_at = datetime.now(UTC)
    await session.flush()
    return mode


# --- Delegation during absence (4.4a, P14-S11) -------------------------


async def create_delegation(
    session: AsyncSession,
    *,
    delegator_principal_id: str,
    deputy_principal_id: str,
    starts_at: datetime,
    ends_at: datetime,
    scope_object_type_ids: list[int] | None,
    scope_process_definition_ids: list[int] | None,
    scope_folder_resource_ids: list[str] | None,
) -> Delegation:
    delegation = Delegation(
        id=str(uuid.uuid4()),
        delegator_principal_id=delegator_principal_id,
        deputy_principal_id=deputy_principal_id,
        starts_at=starts_at,
        ends_at=ends_at,
        scope_object_type_ids=scope_object_type_ids,
        scope_process_definition_ids=scope_process_definition_ids,
        scope_folder_resource_ids=scope_folder_resource_ids,
        created_at=datetime.now(UTC),
    )
    session.add(delegation)
    await session.flush()
    return delegation


async def get_delegation(session: AsyncSession, delegation_id: str) -> Delegation | None:
    return await session.get(Delegation, delegation_id)


async def list_delegations(
    session: AsyncSession,
    *,
    delegator_principal_id: str | None = None,
    deputy_principal_id: str | None = None,
    active_only: bool = False,
) -> list[Delegation]:
    stmt = select(Delegation)
    if delegator_principal_id is not None:
        stmt = stmt.where(Delegation.delegator_principal_id == delegator_principal_id)
    if deputy_principal_id is not None:
        stmt = stmt.where(Delegation.deputy_principal_id == deputy_principal_id)
    stmt = stmt.order_by(Delegation.created_at.desc())
    result = await session.execute(stmt)
    delegations = list(result.scalars().all())
    if active_only:
        now = datetime.now(UTC)
        delegations = [d for d in delegations if is_delegation_active(d, now)]
    return delegations


async def revoke_delegation(
    session: AsyncSession, delegation_id: str, *, revoked_by: str
) -> Delegation:
    delegation = await get_delegation(session, delegation_id)
    if delegation is None:
        raise NotFoundError(f"Delegation {delegation_id!r} unbekannt")
    if delegation.revoked_at is None:
        delegation.revoked_at = datetime.now(UTC)
        delegation.revoked_by = revoked_by
        await session.flush()
    return delegation


def is_delegation_active(delegation: Delegation, now: datetime) -> bool:
    if delegation.revoked_at is not None:
        return False
    return delegation.starts_at <= now <= delegation.ends_at


def _delegation_scope_matches(
    delegation: Delegation,
    *,
    process_definition_id: int | None,
    object_type_id: int | None,
    folder_resource_id: str | None,
) -> bool:
    """A set scope list restricts to exactly these IDs; an empty/``None``
    list means "unrestricted on this dimension". If the corresponding ID of
    the operation being checked is not supplied (the caller doesn't know
    it), a set scope list counts as NOT satisfied (fail closed) - see
    main.py ``GET /delegations/check``."""
    if delegation.scope_process_definition_ids:
        if process_definition_id is None or process_definition_id not in (
            delegation.scope_process_definition_ids
        ):
            return False
    if delegation.scope_object_type_ids:
        if object_type_id is None or object_type_id not in delegation.scope_object_type_ids:
            return False
    if delegation.scope_folder_resource_ids:
        if folder_resource_id is None or folder_resource_id not in (
            delegation.scope_folder_resource_ids
        ):
            return False
    return True


async def is_active_deputy_for(
    session: AsyncSession,
    *,
    deputy_principal_id: str,
    delegator_principal_id: str,
    process_definition_id: int | None = None,
    object_type_id: int | None = None,
    folder_resource_id: str | None = None,
) -> bool:
    """Core of the delegation check (4.4a) - true if at least one active,
    non-revoked delegation from ``delegator_principal_id`` to
    ``deputy_principal_id`` exists whose validity window includes ``now``
    and whose scope (if restricted) matches the operation being checked."""
    delegations = await list_delegations(
        session,
        delegator_principal_id=delegator_principal_id,
        deputy_principal_id=deputy_principal_id,
        active_only=True,
    )
    return any(
        _delegation_scope_matches(
            d,
            process_definition_id=process_definition_id,
            object_type_id=object_type_id,
            folder_resource_id=folder_resource_id,
        )
        for d in delegations
    )
