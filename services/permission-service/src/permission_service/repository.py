import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from permission_service.models import (
    ApprovalActionConfig,
    ApprovalRequest,
    Delegation,
    EffectivePermissionCache,
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


async def update_role(
    session: AsyncSession, role_id: int, *, description: str, permissions: list[str]
) -> Role:
    role = await session.get(Role, role_id)
    if role is None:
        raise NotFoundError(f"role_id {role_id!r} unbekannt")
    role.description = description
    role.permissions = permissions
    await session.flush()
    return role


async def get_role_by_name(session: AsyncSession, name: str) -> Role | None:
    result = await session.execute(select(Role).where(Role.name == name))
    return result.scalars().first()


# Domänengetrennte Admin-Rollen (4.6) - systemeigen (in diesem Service, NICHT
# als Keycloak-Realm-Rollen), damit sie unabhängig vom Identity Provider
# funktionieren. "domain-admin-users" bekam als erste ein zugeordnetes
# technisches Keycloak-Konto + tatsächliche Durchsetzung (siehe auth-service).
# "domain-admin-query-console"/"-manipulate" bekamen ihre tatsächliche
# Durchsetzung in query-service (P8-S1/P8-S2, kein eigenes technisches Konto
# nötig - reine Rollenzuweisung reicht). Die übrigen Domänen sind laut 4.6
# "standardmäßig mitgeliefert", aber bewusst noch ohne Enforcement (deren
# fachliche Funktionen existieren teils noch gar nicht, z. B.
# Lizenzverwaltung). "breakglass-approver" ist keine Domäne aus 4.6, sondern
# die Gruppen-Rolle für die Vier-Augen-Freigabe der Superuser-Aktivierung.
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
    # Ebenfalls kein Konzept-4.6-Domänen-Eintrag, sondern die Auslöse-Rolle für
    # die Notfallsperre (4.8, P6-S6) - bewusst ohne automatisches technisches
    # Konto (wie breakglass-approver, nicht wie users-admin/config-admin):
    # eine Notfallsperre soll einer echten, individuell zurechenbaren Person
    # zugeordnet bleiben, kein geteiltes Konto.
    ("domain-admin-emergency", "Not-Shutdown-Auslösung (4.8)", ["system.not_shutdown.trigger"]),
    # Wie "domain-admin-license" (P9-S1) entsteht diese Domäne erst mit dem
    # tatsächlichen Feature (Plugin Orchestration Service, 3.8, P10-S1) -
    # Konzept 4.6 nennt seine Domänen-Liste ausdrücklich nur beispielhaft.
    ("domain-admin-orchestration", "Plugin-Orchestrierung", ["admin.orchestration"]),
    # Wie "domain-admin-orchestration" (P10-S1) entsteht diese Domäne erst mit
    # dem tatsächlichen Feature (Sensor-Konzept/monitoring-service, 10.1,
    # P11-S1) - Deaktivieren sicherheitsrelevanter Sensoren ist laut Konzept
    # selbst ein sicherheitsrelevanter, auditierter Vorgang.
    ("domain-admin-monitoring", "Monitoring-/Sensor-Konfiguration", ["admin.monitoring"]),
]


async def ensure_domain_admin_roles(session: AsyncSession) -> None:
    for name, description, permissions in DOMAIN_ADMIN_ROLES:
        if await get_role_by_name(session, name) is None:
            await create_role(session, name, description, permissions)


# "everyone"-Gruppe (Post-Roadmap Phase 19 Session 2, ADR 0067): jeder
# authentifizierte Principal ist implizit Mitglied, ohne dass irgendein Konto
# einzeln zugewiesen werden muss - `_collect_effective_roles` unten
# behandelt eine `RoleAssignment` mit genau diesem
# `(principal_type, principal_id)`-Paar an jedem durchlaufenen Resource-Knoten
# als für JEDEN Principal zutreffend. Ersetzt die zuvor hartkodierten
# "jeder authentifizierte Nutzer darf..."-Bypässe in `auth-service`
# (`GET /users/lookup`, `GET /users/directory`, siehe P19-S3) durch eine
# echte, admin-editierbare Rolle - am Ist-Verhalten ändert die Umstellung
# selbst nichts, nur die Durchsetzung wird systemeigen statt fest verdrahtet.
EVERYONE_PRINCIPAL_TYPE = "group"
EVERYONE_PRINCIPAL_ID = "everyone"
EVERYONE_ROLE_NAME = "everyone"
EVERYONE_ROLE_DESCRIPTION = (
    "Jeder authentifizierte Principal (implizite Mitgliedschaft, keine Zuweisung pro Konto nötig)"
)
EVERYONE_ROLE_PERMISSIONS: list[str] = ["users.lookup", "users.directory"]


async def ensure_everyone_role(session: AsyncSession) -> None:
    """Idempotent (gleiches Muster wie `ensure_domain_admin_roles`) - legt
    zusätzlich zur Rolle selbst auch deren `RoleAssignment` an der
    Wurzelressource an, da die "everyone"-Gruppe (anders als Domain-Admin-
    Rollen) kein externes Konto hat, dem die Zuweisung sonst zugeordnet
    würde. Läuft NACH `ensure_root_resource` (braucht `ROOT_RESOURCE_ID` als
    FK-Ziel) und bewusst direkt gegen die Session statt über den
    Vier-Augen-gegateten `POST /role-assignments`-Endpunkt - dies ist
    Bootstrap-Infrastruktur wie `ensure_domain_admin_roles`, keine
    Laufzeit-Admin-Aktion."""
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

    Schließt seit Phase 19 Session 2 zusätzlich Zuweisungen an die
    "everyone"-Gruppe ein (``principal_type="group", principal_id="everyone"``,
    siehe ``ensure_everyone_role``) - jeder authentifizierte Principal gilt
    dafür implizit als Mitglied, unabhängig von seiner eigenen `principal_id`.
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
                or_(
                    RoleAssignment.principal_id == principal_id,
                    and_(
                        RoleAssignment.principal_type == EVERYONE_PRINCIPAL_TYPE,
                        RoleAssignment.principal_id == EVERYONE_PRINCIPAL_ID,
                    ),
                ),
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


async def require_capability(session: AsyncSession, principal_id: str, permission: str) -> None:
    """Direkte Baseline-Rechteprüfung an der Wurzelressource - anders als
    `_require_permission_if_configured` unten (die nur *im Kontext eines
    Approval-Requests* greift) auch für Endpunkte ohne jedes Vier-Augen-
    Zwischenspiel nutzbar (z. B. `POST /maintenance-mode/trigger`, wenn
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
    """Singleton-Zeile (4.8, P6-S6) - wird beim ersten Zugriff angelegt statt
    beim Service-Start unbedingt vorausgesetzt, gleiches Muster wie
    `get_approval_config`s transientes Default-Objekt, nur hier tatsächlich
    persistiert (der Status muss über Neustarts hinweg erhalten bleiben)."""
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


# --- Stellvertretung bei Abwesenheit (4.4a, P14-S11) -------------------------


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
    """Eine gesetzte Scope-Liste schränkt auf genau diese IDs ein; eine leere/
    ``None``-Liste bedeutet "auf dieser Dimension uneingeschränkt". Wird die
    jeweils zugehörige ID des zu prüfenden Vorgangs nicht mitgeliefert
    (Aufrufer kennt sie nicht), gilt eine gesetzte Scope-Liste als NICHT
    erfüllt (fail closed) - siehe main.py ``GET /delegations/check``."""
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
    """Kern der Delegationsprüfung (4.4a) - wahr, wenn mindestens eine aktive,
    nicht widerrufene Delegation von ``delegator_principal_id`` an
    ``deputy_principal_id`` existiert, deren Gültigkeitsfenster ``now``
    einschließt und deren Scope (falls eingeschränkt) zum geprüften Vorgang
    passt."""
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
