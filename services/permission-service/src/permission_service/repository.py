import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, delete, exists, func, literal, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from permission_service import metrics
from permission_service.models import (
    ApprovalActionConfig,
    ApprovalRequest,
    Delegation,
    EffectivePermissionCache,
    Group,
    GroupMembership,
    PermissionCacheGeneration,
    PrincipalCacheGeneration,
    ResourceNode,
    Role,
    RoleAssignment,
    ScopeLock,
    SupervisorAssignment,
    SystemMaintenanceMode,
)
from permission_service.settings import ROOT_RESOURCE_ID

logger = logging.getLogger(__name__)


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


class SelfSupervisionError(Exception):
    """A principal cannot be its own supervisor (P31-S9)."""


class SupervisorCycleError(Exception):
    """Adding this edge would make the new supervisor an indirect report of
    the principal it's meant to supervise, closing a loop in the DAG
    (P31-S9) - see ``create_supervisor_assignment``."""


CACHE_GENERATION_ROW_ID = 1


async def invalidate_cache(session: AsyncSession) -> None:
    """Clears the entire materialized cache (see the docstring on
    ``EffectivePermissionCache`` for the rationale behind the coarse
    granularity) and bumps the cache generation (P84-S1, ADR 0239).

    The bump is what makes invalidation race-free: a concurrent recompute
    that read the old generation may still insert its row after the DELETE
    below (or commit a row the DELETE could not see yet), but that row
    carries the old generation and ``get_effective_permissions`` treats it
    as a miss. The upsert also creates the counter row if it is missing.
    Concurrent writers serialize on that one row lock, readers never touch
    it with a lock."""
    metrics.count_invalidation()
    await session.execute(
        pg_insert(PermissionCacheGeneration)
        .values(id=CACHE_GENERATION_ROW_ID, generation=1)
        .on_conflict_do_update(
            index_elements=["id"],
            set_={"generation": PermissionCacheGeneration.generation + 1},
        )
    )
    await session.execute(delete(EffectivePermissionCache))


async def invalidate_principals(session: AsyncSession, principal_ids: set[str]) -> None:
    """Scoped invalidation (P95-S1d, ADR 0263): bumps the generation of just
    these principals and drops their cache rows; everyone else's entries
    stay valid. The race argument is the global one (ADR 0239) per
    principal: a recompute that read the old principal generation writes a
    row that never counts as a hit. Ids are bumped in sorted order, so two
    concurrent invalidations lock the counter rows in the same order."""
    ids = sorted(principal_ids)
    if not ids:
        return
    metrics.count_invalidation("principal", len(ids))
    stmt = pg_insert(PrincipalCacheGeneration).values(
        [{"principal_id": principal_id, "generation": 1} for principal_id in ids]
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["principal_id"],
            set_={"generation": PrincipalCacheGeneration.generation + 1},
        )
    )
    await session.execute(
        delete(EffectivePermissionCache).where(EffectivePermissionCache.principal_id.in_(ids))
    )


async def _lock_group_members(session: AsyncSession, group_id: str) -> set[str] | None:
    """Locks the group row (FOR UPDATE) and returns its members, or `None`
    for an unknown group. Every change to a group's members or to its role
    assignments takes this lock first (P95-S1d), so the member set an
    assignment change invalidates can't change under it: a member added
    concurrently is invalidated by its own membership change, after."""
    group = await session.get(Group, group_id, with_for_update=True, populate_existing=True)
    if group is None:
        return None
    result = await session.execute(
        select(GroupMembership.principal_id).where(GroupMembership.group_id == group_id)
    )
    return set(result.scalars().all())


async def invalidate_for_assignment(
    session: AsyncSession, principal_type: str, principal_id: str
) -> None:
    """Who a role assignment can affect (P95-S1d): the "everyone" group -
    everyone, so the global generation; another group - its members; a user
    or service - that principal. Lookups match assignments by
    `principal_id` regardless of type (`_collect_effective_roles`), so the
    id itself is always included."""
    if principal_type == EVERYONE_PRINCIPAL_TYPE and principal_id == EVERYONE_PRINCIPAL_ID:
        await invalidate_cache(session)
        return
    affected = {principal_id}
    if principal_type == "group":
        affected |= await _lock_group_members(session, principal_id) or set()
    await invalidate_principals(session, affected)


async def _cache_generations(session: AsyncSession, principal_id: str) -> tuple[int, int]:
    """The global and the principal's generation, in one round trip."""
    row = (
        await session.execute(
            select(
                select(PermissionCacheGeneration.generation)
                .where(PermissionCacheGeneration.id == CACHE_GENERATION_ROW_ID)
                .scalar_subquery(),
                select(PrincipalCacheGeneration.generation)
                .where(PrincipalCacheGeneration.principal_id == principal_id)
                .scalar_subquery(),
            )
        )
    ).one()
    return row[0] or 0, row[1] or 0


@dataclass(slots=True)
class CachedEntry:
    """A valid cache row as the hot path returns it - the same fields as
    `EffectivePermissionCache`, without building an ORM-mapped object per
    row (measurably expensive for a batch of 50, P95-S2b)."""

    principal_id: str
    resource_id: str
    roles: list[str]
    permissions: list[str]
    computed_at: datetime
    generation: int
    principal_generation: int


@dataclass
class CachedLookup:
    """What one read of the hot path returns (P95-S2b): the valid cache
    entries among the requested resources, and whether any scope lock is
    active at all (usually not - then nobody walks the ancestors)."""

    entries: dict[str, CachedEntry]
    any_lock_active: bool


async def cached_effective_permissions(
    engine: AsyncEngine, principal_id: str, resource_ids: list[str]
) -> CachedLookup:
    """The read side of `/check`, `/check/batch` and `/effective-permissions`
    in **one** statement, outside a transaction (P95-S2b, ADR 0257 §3b):
    the global and the principal's generation, the cached rows that match
    both, and whether a scope lock is active. Before, a single check took
    about a dozen round trips (pre-ping, BEGIN, generations, the row through
    the ORM, two queries per ancestor level for scope locks, COMMIT).

    Same validity rule as `get_effective_permissions` (ADR 0239/0263): a row
    counts only while both generations still match - here compared in the
    same snapshot it is read in. Misses are left to the caller, which
    computes them through the unchanged path."""
    cache = EffectivePermissionCache.__table__
    generations = select(
        func.coalesce(
            select(PermissionCacheGeneration.generation)
            .where(PermissionCacheGeneration.id == CACHE_GENERATION_ROW_ID)
            .scalar_subquery(),
            0,
        ).label("g"),
        func.coalesce(
            select(PrincipalCacheGeneration.generation)
            .where(PrincipalCacheGeneration.principal_id == principal_id)
            .scalar_subquery(),
            0,
        ).label("pg"),
        exists()
        .where(ScopeLock.released_at.is_(None))
        .where(or_(ScopeLock.expires_at.is_(None), ScopeLock.expires_at > func.now()))
        .label("locked"),
    ).subquery()
    stmt = select(
        generations.c.locked,
        cache.c.resource_id,
        cache.c.roles,
        cache.c.permissions,
        cache.c.computed_at,
        cache.c.generation,
        cache.c.principal_generation,
    ).select_from(
        generations.outerjoin(
            cache,
            and_(
                cache.c.principal_id == literal(principal_id),
                cache.c.resource_id.in_(resource_ids),
                cache.c.generation == generations.c.g,
                cache.c.principal_generation == generations.c.pg,
            ),
        )
    )
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        rows = (await conn.execute(stmt)).all()
    entries = {}
    for row in rows:
        if row.resource_id is None:
            continue
        entries[row.resource_id] = CachedEntry(
            principal_id=principal_id,
            resource_id=row.resource_id,
            roles=row.roles,
            permissions=row.permissions,
            computed_at=row.computed_at,
            generation=row.generation,
            principal_generation=row.principal_generation,
        )
    # Counted once per read, not per row (a per-row sensor call was 5 % of a
    # batch's CPU).
    metrics.count_lookup(hit=True, n=len(entries))
    return CachedLookup(entries=entries, any_lock_active=bool(rows and rows[0].locked))


async def current_cache_generation(session: AsyncSession) -> int:
    """The current cache generation; 0 before the first invalidation ever."""
    value = await session.scalar(
        select(PermissionCacheGeneration.generation).where(
            PermissionCacheGeneration.id == CACHE_GENERATION_ROW_ID
        )
    )
    return value or 0


async def ensure_root_resource(session: AsyncSession) -> None:
    existing = await session.get(ResourceNode, ROOT_RESOURCE_ID)
    if existing is None:
        session.add(
            ResourceNode(resource_id=ROOT_RESOURCE_ID, parent_id=None, resource_type="root")
        )
        await session.flush()


async def create_resource_node(
    session: AsyncSession, resource_id: str, parent_id: str | None, resource_type: str = "folder"
) -> ResourceNode:
    """Idempotent create-if-missing (Post-Roadmap Phase 35 Session 2, ADR
    0144) - shared by `structure_consumer.py`'s `"*.resource.created"`
    handler (async, via NATS) and `POST /resources` (new, synchronous REST
    path added specifically for `case-service`, whose own per-case RBAC
    checks need the node to exist by the time case creation RETURNS, not
    eventually - see ADR 0144 "Rationale" for why a purely event-driven
    registration, sufficient for `folder-service`, was not sufficient
    here). Returns the existing node unchanged if one already exists
    (matches the event handler's own prior "if missing, insert" behavior
    exactly - callers on either path never overwrite an existing node's
    `parent_id`/`resource_type`).

    `parent_id` falls back to `ROOT_RESOURCE_ID` if it doesn't correspond to
    an existing node (Post-Roadmap Phase 39 Session 4, ADR 0154 - found via
    live verification of document-service's new startup backfill loop: a
    real installation can have a folder whose OWN `ResourceNode` was, for
    whatever historical reason, never registered - `folder-service`'s
    purely event-driven registration has no synchronous guarantee the way
    `case-service`'s/`document-service`'s own registrations do. Without
    this fallback, a single such orphaned folder reference would raise an
    unhandled `ForeignKeyViolationError` and could crash the CALLING
    service's entire startup, not just fail one document's registration -
    a disproportionate blast radius for what is, functionally, the exact
    same "unregistered resource" case this whole mechanism already handles
    everywhere else by falling back to root/denying gracefully, never by
    crashing)."""
    existing = await session.get(ResourceNode, resource_id)
    if existing is not None:
        return existing
    if parent_id is not None and parent_id != ROOT_RESOURCE_ID:
        parent_exists = await session.get(ResourceNode, parent_id)
        if parent_exists is None:
            logger.warning(
                "create_resource_node: parent_id=%r hat keinen eigenen ResourceNode - "
                "resource_id=%r wird stattdessen unter root registriert",
                parent_id,
                resource_id,
            )
            parent_id = ROOT_RESOURCE_ID
    node = ResourceNode(resource_id=resource_id, parent_id=parent_id, resource_type=resource_type)
    session.add(node)
    await session.flush()
    return node


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


class ProtectedRoleError(Exception):
    """A seeded role the system relies on can't be deleted."""


# Roles the system seeds and relies on (P94-S3): never deletable. The
# teamspace roles are created by teamspace-service at its startup.
def protected_role_names() -> frozenset[str]:
    return frozenset(
        {name for name, _, _ in DOMAIN_ADMIN_ROLES}
        | {EVERYONE_ROLE_NAME, "teamspace-member", "teamspace-manager"}
    )


async def delete_role(session: AsyncSession, role_id: int) -> Role:
    """Deletes a role with its assignments (P94-S3) - test runs had left
    ~2,600 roles behind and there was no way to remove one. Invalidates the
    effective-permissions cache like every other permission change."""
    role = await session.get(Role, role_id)
    if role is None:
        raise NotFoundError(f"role_id {role_id!r} unbekannt")
    if role.name in protected_role_names():
        raise ProtectedRoleError(
            f"Rolle {role.name!r} ist systemseitig angelegt und nicht löschbar"
        )
    await session.execute(delete(RoleAssignment).where(RoleAssignment.role_id == role_id))
    await session.delete(role)
    await session.flush()
    await invalidate_cache(session)
    return role


async def delete_approval_config(session: AsyncSession, action_type: str) -> None:
    """Removes an action type's four-eyes row (P94-S3); without one,
    `requires_approval=False` applies, as for any unconfigured type."""
    row = await session.get(ApprovalActionConfig, action_type)
    if row is None:
        raise NotFoundError(f"Keine Freigabe-Konfiguration für {action_type!r}")
    await session.delete(row)
    await session.flush()


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
    # P85-S2 (ADR 0240): force-releasing another person's document lock (4.2)
    # had no permission check at all - its own domain, not folded into
    # "domain-admin-document-config" (configuration, not intervention).
    ("domain-admin-document-locks", "Dokumentsperren-Verwaltung", ["admin.document_lock"]),
    # Post-Roadmap Phase 22 Session 5: `teamspace-service`'s new
    # installation-wide overview endpoint (`GET /admin/teamspaces`, shows
    # ALL teamspaces, not just those of the requesting principal like
    # `GET /teamspaces`) is the first place in this service that needs a
    # real permission check - teamspaces themselves remain self-managed
    # (2.5, no capability gate for creation/joining).
    ("domain-admin-teamspaces", "Teamspace-Aufsicht", ["admin.teamspace_management"]),
    # Post-Roadmap Phase 31 Session 3 (ADR 0114): classification level
    # becomes a genuine per-document/per-version attribute instead of a
    # fixed object-type default - deliberately a NEW domain, separate from
    # "domain-admin-config" (`admin.object_config`, which still governs the
    # object type's own classification_level default/seed value): setting or
    # raising a specific document's actual classification is a materially
    # different, more sensitive action than editing object-type schemas in
    # general, matching the "domain-admin-legal-hold" precedent (a dedicated
    # domain rather than folding a security-sensitive per-document action
    # into a broader existing one).
    (
        "domain-admin-classification",
        "Einstufungsverwaltung (Verschlusssachen)",
        ["admin.classification"],
    ),
    # Post-Roadmap Phase 31 Session 5 (ADR 0116): records quarantine (an
    # administered holding area with restricted visibility and a
    # configurable auto-delete condition) reuses the "domain-admin-legal-
    # hold" PATTERN (a dedicated domain-admin capability via
    # `has_permission`), but with its OWN capability - not literally
    # `admin.legal_hold` - since setting/releasing a records quarantine is a
    # materially different, separately-grantable action from legal hold
    # (one schedules destruction, the other prevents it indefinitely; an
    # installation may want different people responsible for each).
    (
        "domain-admin-records-quarantine",
        "Schriftgutquarantäne-Verwaltung",
        ["admin.records_quarantine"],
    ),
    # Post-Roadmap Phase 38 Session 2: `document-service`'s three disposal
    # callbacks (`PUT /documents/{id}/archived`/`dehydrated`/`rehydrated`)
    # previously had NO caller check at all, relying purely on network
    # topology - `archival-service` is the one legitimate caller and has no
    # natural per-request human principal, so it asserts the fixed service
    # identity `X-DMS-Principal: archival-service`. Deliberately not named
    # "domain-admin-..." like the other entries in this list - this is a
    # machine-to-machine service capability, not a human-administered
    # domain, but reuses the same seeded-role mechanism (auto-created on
    # every fresh installation via `ensure_domain_admin_roles`, no manual
    # per-installation grant needed) rather than the ad-hoc throwaway-role
    # pattern used for `notification-service`'s `reporting-service-
    # scheduler` (that one had no existing seeded-role convention to join;
    # document-service already had one).
    (
        "archival-service-callback",
        "Aussonderungs-Callback (archival-service)",
        ["document.disposal_callback"],
    ),
    # Post-Roadmap Phase 38 Session 3: `PUT /documents/{id}/retention`/
    # `PUT /retention-config`/`PUT /trash-config` on document-service AND
    # the identical three endpoints on folder-service previously had NO
    # permission check at all - one shared domain across both services
    # (the same retention/disposal-policy concern for documents and
    # folders), separate from `admin.legal_hold` (a hold PREVENTS deletion,
    # retention administration SCHEDULES it - conceptually opposite
    # actions, same reasoning ADR 0075 itself already used).
    ("domain-admin-retention", "Aufbewahrungsverwaltung", ["admin.retention"]),
    # Same session: document-service's remaining settings pages (`upload-
    # config`/`export-config`/`audit-trace-config`/`audit-trace-role-
    # overrides`/`share-link-config`) previously had NO permission check
    # either - one shared capability across all of them (all installation-
    # wide configuration knobs owned by this one service), rather than one
    # capability per settings page.
    ("domain-admin-document-config", "Dokumentendienst-Konfiguration", ["admin.document_config"]),
    # Same session: signature-service's `PUT /signature-config` previously
    # had NO permission check - a dedicated domain rather than reusing
    # `admin.object_config`/`admin.storage`, since electronic-signature
    # provider configuration (3.10) is a materially different, more
    # specialized concern than either object-type schema or storage-backend
    # administration.
    ("domain-admin-signature", "Signatur-Konfiguration", ["admin.signature_config"]),
    # Same session: notification-service's `EmailTemplate` CRUD previously
    # had NO permission check - deliberately a separate domain from P38-S2's
    # own `notification.write` (that one governs who may TRIGGER a
    # notification, this one governs who may change the wording every
    # future notification is sent with).
    ("domain-admin-notification", "Notification-Konfiguration", ["admin.notification_config"]),
    # P88-S4a (ADR 0244): mail-connector's mailboxes moved from DMS_MAILBOXES
    # into a table managed through the admin UI. Their credentials grant
    # access to every incoming letter, so managing them is its own domain,
    # not part of the mail room role (`dms-poststelle`, which processes the
    # intake) nor of notification templates.
    ("domain-admin-mail", "Postfach-Konfiguration", ["admin.mail_config"]),
    # Phase 59 Session 1: `GET /notifications`/`GET /notifications/{id}` on
    # notification-service previously had NO permission check at all -
    # complete unauthenticated PII/content disclosure (any authenticated
    # principal could enumerate every notification ever sent, including
    # break-glass superuser activation emails). Deliberately a THIRD,
    # separate notification-service capability, not reused with
    # `notification.write` (governs who may TRIGGER one) or
    # `admin.notification_config` (governs template wording) - reading the
    # delivery log is a different concern/risk profile from either, same
    # split-by-concern precedent this project already uses repeatedly
    # (legal_hold vs. deletion, pseudonymization vs. reveal).
    (
        "domain-admin-notification-read",
        "Notification-Protokoll einsehen",
        ["admin.notification_read"],
    ),
    # Post-Roadmap Phase 41 Session 2 (ADR 0156): concept 5.2's GDPR-tension
    # fix (pseudonymize individual personal-data attributes instead of
    # hard-deleting a whole document under a retention obligation).
    # Deliberately TWO separate capabilities, not one, extending the
    # "domain-admin-legal-hold"/"domain-admin-deletion" split once more:
    # pseudonymizing REDUCES exposure of personal data (comparatively
    # low-risk), revealing the original value back EXPOSES it again
    # (materially higher-risk) - an installation may want different people
    # responsible for each.
    (
        "domain-admin-pseudonymization",
        "Attribut-Pseudonymisierung",
        ["admin.attribute_pseudonymization"],
    ),
    (
        "domain-admin-pii-reveal",
        "Pseudonymisierte Attribute aufdecken",
        ["admin.attribute_reveal"],
    ),
    # Post-Roadmap Phase 41 Session 3 (ADR 0157): concept 5.5's fine-
    # grained user tracking. Deliberately TWO separate capabilities, not
    # one, same asymmetric-risk split as P41-S2's pseudonymize/reveal pair
    # above: toggling tracking REDUCES what's captured going forward
    # (comparatively low-risk), viewing already-collected session data
    # (client IP, User-Agent, auth method per login) EXPOSES behavioral
    # information about a specific principal (materially higher-risk).
    (
        "domain-admin-user-tracking",
        "Nutzer-Tracking-Verwaltung",
        ["admin.user_tracking"],
    ),
    (
        "domain-admin-user-tracking-view",
        "Nutzer-Tracking einsehen",
        ["admin.user_tracking_view"],
    ),
    # Phase 50 Session 2: `notification-service`/`signature-service` each
    # independently authenticated as the `users-admin` technical account
    # (full domain-admin: user CRUD + AD-group->role mapping control) just
    # to do a read-only `GET /users` lookup - a real excess-privilege
    # exposure, not merely inelegant (a credential leak of either service
    # would grant full user-management control, not just directory-read).
    # Same pattern as `archival-service-callback` above: a dedicated seeded
    # role for a machine-to-machine identity, not named "domain-admin-...".
    # Both services assert their own fixed identity (`X-DMS-Principal:
    # notification-service`/`signature-service`) rather than sharing one -
    # more auditable, and consistent with how `signature-service` already
    # identifies itself to `document-service`.
    (
        "service-user-lookup",
        "Nutzerverzeichnis-Abfrage (Service-zu-Service)",
        ["service.user_lookup"],
    ),
    # P90-S3 (ADR 0247): who is told about a new four-eyes request when its
    # action type names neither a recipient role nor a required permission
    # ("any second person" may decide - notifying everyone would be noise).
    # Only notification: holding it grants no right to decide.
    (
        "approval-recipients",
        "Freigabe-Benachrichtigung",
        ["approval.notify"],
    ),
    # Post-Roadmap Phase 74 Session 3 (ADR 0160/ADR 0217): the specific
    # missing piece a teamspace AD-group invitation needs - "who is
    # currently a member of AD group X," as a queryable list
    # (`auth-service`'s new `GET /groups/{name}/members`). Same
    # "dedicated seeded role for a machine-to-machine identity, not
    # 'domain-admin-...'" shape as `service-user-lookup` immediately
    # above - `teamspace-service` asserts its own fixed identity
    # (`X-DMS-Principal: teamspace-service`, already used elsewhere in
    # this service, see `clients.py`) rather than needing
    # `admin.user_management`, the far broader capability a raw group
    # roster otherwise has no narrower home under.
    (
        "service-group-lookup",
        "Gruppenmitglieder-Abfrage (Service-zu-Service)",
        ["service.group_lookup"],
    ),
    # Phase 59 Session 5: `POST`/`DELETE /paired-installations` previously
    # had no permission check at all (only `license_gate`) - any licensed
    # caller could pair with an arbitrary, attacker-controlled installation.
    # Dedicated new domain rather than reusing an unrelated one, same
    # precedent as `domain-admin-legal-hold`/`domain-admin-records-
    # quarantine` (a new capability domain when a genuinely new admin
    # concern ships, not a forced reuse).
    (
        "domain-admin-migration",
        "Migrations-Installationspaarung verwalten",
        ["admin.migration_management"],
    ),
    # Phase 61 Session 3: `config-service`'s `GET /config/export`/`POST
    # /config/compare` previously had no permission check at all, despite
    # returning reconnaissance-grade access-control information (full
    # role/permission catalog, AD-group->role mapping, Keycloak realm
    # roles, BPMN definitions) to any authenticated caller. A dedicated
    # READ capability, distinct from `admin.object_config` (which already
    # gates `POST /config/import`, a WRITE action) - same read/write split
    # convention as `admin.notification_read` vs. `notification.write`.
    (
        "domain-admin-config-read",
        "Konfiguration einsehen (Export/Vergleich)",
        ["admin.config_read"],
    ),
    # P70-S2 (ADR 0204): document declassification (14.2) -
    # deliberately a NEW domain, separate from `admin.classification`
    # (which only ever RAISES a document's classification) and from
    # `admin.deletion_classified` (which governs PURGING an already-
    # classified document, a materially different sensitive action, same
    # "two distinct sensitive actions, two distinct domains" reasoning
    # ADR 0114 itself already used). Same single-capability shape as
    # "breakglass-approver"/`breakglass.approve` (4.6) - `_require_
    # permission_if_configured` (see `create_approval_request`/
    # `approve_request` above) checks THIS SAME capability on both the
    # initiator and the approver, there is no separate "approver-only"
    # capability mechanism in this codebase. The actual "two distinct
    # people" guarantee comes from `approve_request`'s unconditional
    # `approved_by == request.initiated_by` rejection, not from two
    # different capabilities - an earlier draft of this entry mistakenly
    # assumed a separate approver capability existed; corrected before
    # this session's own tests caught the resulting 403 in
    # `create_approval_request` (the initiator itself failing its own
    # `required_permission` check).
    (
        "domain-admin-declassification",
        "Deklassifizierungsverwaltung (Verschlusssachen)",
        ["admin.declassification"],
    ),
    # P71-S2: `folder-service`'s new `GET`/`PUT /audit-trace-config`
    # (folder.viewed logging toggle, mirroring `document-service`'s own
    # `admin.document_config`-gated settings pages, ADR 0148 precedent -
    # "one capability per owning service for its own settings pages, not
    # per page") - folder-service had no such catch-all settings
    # capability of its own before this, only the shared cross-service
    # `admin.retention` (which covers a materially different concern,
    # retention/disposal policy, not audit-trace depth).
    (
        "domain-admin-folder-config",
        "Ordnerdienst-Konfiguration",
        ["admin.folder_config"],
    ),
    # P93-S2 (ADR 0249): skipping a step, sending back, or moving a running
    # process instance to a newer definition version - an intervention in
    # running work, separate from defining processes ("admin.object_config").
    (
        "domain-admin-workflow-instances",
        "Laufende Prozessinstanzen ändern",
        ["workflow.instance_modify"],
    ),
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
    # Post-Roadmap Phase 38 Session 2: virus-scan-service's `POST /scan`/
    # `GET /scans/{id}`/`GET /scans` (without `status=infected`, which
    # already had its own narrower `admin.quarantine` gate since ADR 0073)
    # had NO permission check at all - deliberately deferred back at ADR
    # 0073 itself ("no full retrofit of the remaining endpoints"), now
    # closed. Same precedent as ocr.read/write and rendering.read/write
    # (ADR 0073, same session) - "everyone" preserves the previous
    # de-facto-open behavior (no frontend currently calls these two
    # endpoints with any expectation of a permission wall) while making it
    # admin-editable.
    "virus_scan.read",
    "virus_scan.write",
    # Post-Roadmap Phase 38 Session 2: audit-service's GET /events/.../verify
    # previously had NO RBAC check whatsoever (not even "must be an
    # authenticated principal" - readable by anyone with plain network
    # access). Same precedent as ADR 0072/0073/0074 above: "everyone"
    # preserves the previous de-facto-open-to-any-logged-in-user behavior
    # (already the case in practice, since reporting-service's own
    # forensic trace surfaces materially the same underlying event data via
    # the already-everyone-granted `reporting.forensic_trace`) while closing
    # the actual gap (direct, unauthenticated network access) and making the
    # permission admin-editable going forward.
    "audit.read",
    # Post-Roadmap Phase 38 Session 4 (ADR 0149): `folder-service`'s core
    # folder CRUD and `document-service`'s primary document read/write
    # paths previously had NO permission check at all (not the "checked
    # but too permissive" case the entries above describe - these two
    # services simply never called `permission-service` for their main
    # endpoints). Retrofitting a REAL `document.read`/`.write`/
    # `folder.read`/`.write` check without also granting these to
    # "everyone" would 403 every regular, non-teamspace, non-shared
    # document/folder for everyone except its literal creator (there is
    # no "creator gets an automatic grant" mechanism in this project) -
    # a system-breaking regression, not a security fix. "everyone"
    # preserves the previous de-facto-open behavior for the vast
    # majority of ordinary folders/documents (unchanged from before this
    # session, modulo now requiring a valid `X-DMS-Principal`), while a
    # teamspace's root folder is deliberately excluded from this via
    # `inherit=False` on its own `ResourceNode` (ADR 0043's existing
    # per-member role assignment already handles teamspace access
    # correctly; see `docs/adr/0149-...md` for the full mechanism).
    "document.read",
    "document.write",
    "folder.read",
    "folder.write",
    # Post-Roadmap Phase 44 Session 2 (ADR 0163): `folder-service`'s
    # `DELETE /folders/{id}`/`POST /folders/{id}/trash` now check the
    # dedicated `folder.delete` permission instead of the broader
    # `folder.write` above, specifically so a teamspace's non-manager
    # members (who DO get `folder.write` via `teamspace-member`, but
    # deliberately not `folder.delete`) can no longer delete/trash the
    # entire teamspace by calling folder-service directly, bypassing
    # `teamspace-service`'s own manager-only guard. Granting it to
    # "everyone" here preserves today's default-open delete/trash
    # behavior for every ordinary, non-teamspace folder - this is a
    # narrowing for teamspaces specifically (via their root folder's
    # `inherit=False` isolation), not a new restriction project-wide.
    "folder.delete",
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
async def create_group(
    session: AsyncSession, name: str, description: str, *, is_org_unit: bool = False
) -> Group:
    group = Group(
        id=str(uuid.uuid4()),
        name=name,
        description=description,
        created_at=datetime.now(UTC),
        is_org_unit=is_org_unit,
    )
    session.add(group)
    await session.flush()
    return group


async def list_groups(session: AsyncSession) -> list[Group]:
    result = await session.execute(select(Group))
    return list(result.scalars().all())


async def set_group_org_unit_flag(session: AsyncSession, group_id: str, is_org_unit: bool) -> Group:
    """Post-Roadmap Phase 35 Session 1 (ADR 0143) - the only field of an
    existing `Group` that can be changed after creation; groups had no
    update endpoint at all before this session (create/list/delete/members
    only)."""
    group = await session.get(Group, group_id)
    if group is None:
        raise NotFoundError(f"group_id {group_id!r} unbekannt")
    group.is_org_unit = is_org_unit
    await session.flush()
    return group


async def delete_group(session: AsyncSession, group_id: str) -> None:
    """Deletes the group along with its memberships. Deliberately NO check
    for still-existing `RoleAssignment` rows pointing at this group
    (``principal_id=group_id``) - such a row simply no longer matches any
    principal afterward (empty member list), the same behavior as a group
    that never had any members assigned. Consistent with `Role`, which
    likewise has no delete endpoint/reference check."""
    members = await _lock_group_members(session, group_id)
    if members is None:
        raise NotFoundError(f"group_id {group_id!r} unbekannt")
    group = await session.get(Group, group_id)
    await session.execute(delete(GroupMembership).where(GroupMembership.group_id == group_id))
    await session.delete(group)
    # Only its members lose what the group gave them (P95-S1d).
    await invalidate_principals(session, members | {group_id})


async def add_group_member(
    session: AsyncSession, group_id: str, principal_id: str
) -> GroupMembership:
    if await _lock_group_members(session, group_id) is None:
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
    # Only the new member's rights change (P95-S1d).
    await invalidate_principals(session, {principal_id})
    return membership


async def list_group_members(session: AsyncSession, group_id: str) -> list[GroupMembership]:
    result = await session.execute(
        select(GroupMembership).where(GroupMembership.group_id == group_id)
    )
    return list(result.scalars().all())


async def remove_group_member(session: AsyncSession, group_id: str, principal_id: str) -> None:
    # The group lock first, as for every membership change (P95-S1d).
    await _lock_group_members(session, group_id)
    result = await session.execute(
        select(GroupMembership).where(
            GroupMembership.group_id == group_id, GroupMembership.principal_id == principal_id
        )
    )
    membership = result.scalars().first()
    if membership is None:
        raise NotFoundError(f"{principal_id!r} ist kein Mitglied von Gruppe {group_id!r}")
    await session.delete(membership)
    await invalidate_principals(session, {principal_id})


async def create_supervisor_assignment(
    session: AsyncSession, principal_id: str, supervisor_principal_id: str
) -> SupervisorAssignment:
    """Org-hierarchy foundation (P31-S9). Idempotent on an exact duplicate
    (same precedent as ``add_group_member``); rejects self-supervision and
    any assignment that would close a cycle in the DAG (a cycle would make
    ``get_supervisor_chain`` loop forever without the defensive visited-set
    there, and would corrupt P31-S10's chain-based grant resolution).
    Deliberately no ``invalidate_cache`` call - unlike ``Group``, this table
    doesn't feed ``_collect_effective_roles``/the permission cache (same as
    ``Delegation``, which likewise doesn't invalidate it)."""
    if principal_id == supervisor_principal_id:
        raise SelfSupervisionError(f"{principal_id!r} kann nicht die eigene Führungskraft sein")
    existing = await session.execute(
        select(SupervisorAssignment).where(
            SupervisorAssignment.principal_id == principal_id,
            SupervisorAssignment.supervisor_principal_id == supervisor_principal_id,
        )
    )
    assignment = existing.scalars().first()
    if assignment is not None:
        return assignment
    chain_above_supervisor = await get_supervisor_chain(session, supervisor_principal_id)
    if principal_id in chain_above_supervisor:
        raise SupervisorCycleError(
            f"Zuordnung abgelehnt: {supervisor_principal_id!r} berichtet bereits "
            f"(direkt oder indirekt) an {principal_id!r} - diese Zuordnung würde einen "
            "Zyklus erzeugen"
        )
    assignment = SupervisorAssignment(
        principal_id=principal_id,
        supervisor_principal_id=supervisor_principal_id,
        created_at=datetime.now(UTC),
    )
    session.add(assignment)
    await session.flush()
    return assignment


async def list_supervisor_assignments(
    session: AsyncSession,
    *,
    principal_id: str | None = None,
    supervisor_principal_id: str | None = None,
) -> list[SupervisorAssignment]:
    """Direct supervisors of ``principal_id`` (P31-S10's grant resolution),
    or direct reports of ``supervisor_principal_id`` (P31-S11's "who reports
    to me" oversight view) - same optional-filter shape as
    ``list_role_assignments``. Unfiltered: every assignment, the admin
    overview listing."""
    query = select(SupervisorAssignment)
    if principal_id is not None:
        query = query.where(SupervisorAssignment.principal_id == principal_id)
    if supervisor_principal_id is not None:
        query = query.where(SupervisorAssignment.supervisor_principal_id == supervisor_principal_id)
    result = await session.execute(query)
    return list(result.scalars().all())


async def delete_supervisor_assignment(session: AsyncSession, assignment_id: int) -> None:
    assignment = await session.get(SupervisorAssignment, assignment_id)
    if assignment is None:
        raise NotFoundError(f"supervisor_assignment {assignment_id!r} unbekannt")
    await session.delete(assignment)


async def get_supervisor_chain(session: AsyncSession, principal_id: str) -> set[str]:
    """All transitive supervisors of ``principal_id`` (P31-S9, prerequisite
    for P31-S10's "grant the full supervisor chain access"). A DAG breadth-
    first union, not a single-line tree walk: ``principal_id`` may have
    several direct supervisors (matrix reporting), and their own chains may
    reconverge (e.g. a diamond shape) - ``chain`` accumulates the union of
    every upward path, each principal visited at most once. The visited-set
    (``chain``) doubles as cycle protection even though
    ``create_supervisor_assignment`` already rejects cycles at write time -
    defensive, not load-bearing, in the normal case."""
    chain: set[str] = set()
    frontier = {principal_id}
    while frontier:
        result = await session.execute(
            select(SupervisorAssignment.supervisor_principal_id).where(
                SupervisorAssignment.principal_id.in_(frontier)
            )
        )
        next_frontier = {row[0] for row in result.all()} - chain - {principal_id}
        chain |= next_frontier
        frontier = next_frontier
    return chain


async def _group_ids_for_principal(session: AsyncSession, principal_id: str) -> set[str]:
    result = await session.execute(
        select(GroupMembership.group_id).where(GroupMembership.principal_id == principal_id)
    )
    return set(result.scalars().all())


async def _org_unit_group_ids_for_principal(session: AsyncSession, principal_id: str) -> set[str]:
    """Post-Roadmap Phase 35 Session 1 (ADR 0143) - the `is_org_unit`-flagged
    subset of `_group_ids_for_principal`'s result, used by
    `create_org_hierarchy_grant`'s `"org_unit"` branch. Deliberately a
    separate function rather than a parameter on `_group_ids_for_principal`
    itself - that helper has one other caller (`create_delegation`'s
    scope-resolution family is unrelated) that must keep seeing every group,
    not just org units."""
    result = await session.execute(
        select(GroupMembership.group_id)
        .join(Group, Group.id == GroupMembership.group_id)
        .where(GroupMembership.principal_id == principal_id, Group.is_org_unit.is_(True))
    )
    return set(result.scalars().all())


async def create_role_assignment(
    session: AsyncSession, *, principal_type: str, principal_id: str, role_id: int, resource_id: str
) -> RoleAssignment:
    """Concurrency-safe on the exact same `uq_assignment` collision this
    session found live in the regression suite: two concurrent callers
    both listing "does this assignment already exist?" as empty, then both
    attempting to create it, is a real TOCTOU race every caller of this
    endpoint hits (every test fixture and admin-ui flow in this project
    grants roles this same "list first, create if missing" way, with no
    coordination between callers) - previously an unhandled
    `IntegrityError` (500) for whichever request lost the race. Same
    `INSERT ... ON CONFLICT DO NOTHING` + re-read pattern already
    established for `get_effective_permissions`'s cache-population race
    above (P31-S4/ADR 0115) - "someone else already created the identical
    assignment" is a fine outcome to just read back, not an error."""
    resource = await session.get(ResourceNode, resource_id)
    if resource is None:
        raise NotFoundError(f"resource_id {resource_id!r} unbekannt")
    role = await session.get(Role, role_id)
    if role is None:
        raise NotFoundError(f"role_id {role_id!r} unbekannt")

    insert_stmt = (
        pg_insert(RoleAssignment)
        .values(
            principal_type=principal_type,
            principal_id=principal_id,
            role_id=role_id,
            resource_id=resource_id,
        )
        .on_conflict_do_nothing(
            index_elements=["principal_type", "principal_id", "role_id", "resource_id"]
        )
    )
    await session.execute(insert_stmt)
    await session.flush()
    assignment = await session.scalar(
        select(RoleAssignment).where(
            RoleAssignment.principal_type == principal_type,
            RoleAssignment.principal_id == principal_id,
            RoleAssignment.role_id == role_id,
            RoleAssignment.resource_id == resource_id,
        )
    )
    assert assignment is not None
    await invalidate_for_assignment(session, principal_type, principal_id)
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


async def delete_role_assignment(session: AsyncSession, assignment_id: int) -> RoleAssignment:
    """Returns the now-deleted row (P71-S2, for the caller's own
    `permission.role_assignment.deleted` event payload) - fields are read
    before `session.delete()` to avoid relying on SQLAlchemy's post-delete
    attribute-expiry behavior."""
    assignment = await session.get(RoleAssignment, assignment_id)
    if assignment is None:
        raise NotFoundError(f"role_assignment {assignment_id!r} unbekannt")
    deleted = RoleAssignment(
        id=assignment.id,
        principal_type=assignment.principal_type,
        principal_id=assignment.principal_id,
        role_id=assignment.role_id,
        resource_id=assignment.resource_id,
    )
    await session.delete(assignment)
    await invalidate_for_assignment(session, deleted.principal_type, deleted.principal_id)
    return deleted


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
) -> tuple[list[Role], bool]:
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

    Also returns whether ``resource_id`` itself is registered (P89-S4, ADR
    0254): an unregistered resource denies everything, and that answer must
    not be cached - see ``get_effective_permissions``.
    """
    collected: dict[int, Role] = {}
    current_id: str | None = resource_id
    member_group_ids = await _group_ids_for_principal(session, principal_id)
    registered = False

    while current_id is not None:
        node = await session.get(ResourceNode, current_id)
        if node is None:
            break
        if current_id == resource_id:
            registered = True

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

    return list(collected.values()), registered


async def get_effective_permissions(
    session: AsyncSession, principal_id: str, resource_id: str
) -> EffectivePermissionCache:
    # The generation is read BEFORE anything else (P84-S1, ADR 0239): if a
    # permission change commits while this request computes, the entry
    # written below carries the old generation and is ignored from then on.
    # `populate_existing` because an earlier lookup in the same session may
    # already hold a now-outdated row in the identity map.
    # Since P95-S1d (ADR 0263) the principal's own generation too: both are
    # read first, and an entry counts only while both still match.
    generation, principal_generation = await _cache_generations(session, principal_id)
    cached = await session.get(
        EffectivePermissionCache, (principal_id, resource_id), populate_existing=True
    )
    if (
        cached is not None
        and cached.generation == generation
        and cached.principal_generation == principal_generation
    ):
        metrics.count_lookup(hit=True)
        return cached

    metrics.count_lookup(hit=False)
    # Timed only while the (expensive) sensor is on - not even the clock
    # read otherwise (10.1).
    started = time.monotonic() if metrics.rebuild_timing_active() else None
    roles, registered = await _collect_effective_roles(session, principal_id, resource_id)
    permissions = sorted({p for role in roles for p in role.permissions})
    roles_out = sorted(role.name for role in roles)
    computed_at = datetime.now(UTC)
    if not registered:
        # P89-S4 (ADR 0254): the deny for a not-yet-registered resource is
        # not cached. That is what lets registering a node skip cache
        # invalidation: no cached row for it can exist, so there is nothing
        # to go stale - and a recompute racing the registration either saw
        # no node (and caches nothing) or saw it (and caches the right
        # answer). Registration happens once per document/folder/case, so
        # these misses are rare; invalidating instead cleared the whole
        # cache twice per upload.
        return EffectivePermissionCache(
            principal_id=principal_id,
            resource_id=resource_id,
            roles=roles_out,
            permissions=permissions,
            computed_at=computed_at,
            generation=generation,
            principal_generation=principal_generation,
        )
    # Concurrency-safe cache population (found live during post-roadmap
    # phase 31 session 4's browser verification, ADR 0115 - two of the new
    # redaction-preview endpoints checking `document.read` on the same
    # resource in quick succession triggered this on a real, previously
    # unexercised cold-cache race): `session.merge()` is NOT atomic - it
    # does an internal existence check, then either UPDATE or INSERT, so two
    # concurrent cache-miss requests for the same `(principal_id,
    # resource_id)` can both compute a fresh entry and both attempt an
    # INSERT, the second failing on the composite primary key. Same
    # `INSERT ... ON CONFLICT DO NOTHING` + re-read pattern already
    # established for object-type-service's counter rows (P5e-S1).
    #
    # Since P84-S1 (ADR 0239) it is an upsert that only ever moves an entry
    # FORWARD in generation: an existing row from an older generation (left
    # behind by a race, see `invalidate_cache`) is replaced, while a row a
    # concurrent request already wrote for the same or a newer generation is
    # kept. The function returns what it computed itself rather than
    # re-reading - a re-read could return a row another request wrote under
    # a different generation.
    insert_stmt = pg_insert(EffectivePermissionCache).values(
        principal_id=principal_id,
        resource_id=resource_id,
        roles=roles_out,
        permissions=permissions,
        computed_at=computed_at,
        generation=generation,
        principal_generation=principal_generation,
    )
    insert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=["principal_id", "resource_id"],
        set_={
            "roles": insert_stmt.excluded.roles,
            "permissions": insert_stmt.excluded.permissions,
            "computed_at": insert_stmt.excluded.computed_at,
            "generation": insert_stmt.excluded.generation,
            "principal_generation": insert_stmt.excluded.principal_generation,
        },
        # Forward only, in both generations together (row comparison).
        where=tuple_(
            EffectivePermissionCache.generation, EffectivePermissionCache.principal_generation
        )
        < tuple_(insert_stmt.excluded.generation, insert_stmt.excluded.principal_generation),
    )
    await session.execute(insert_stmt)
    await session.flush()
    if started is not None:
        metrics.observe_rebuild(time.monotonic() - started)
    return EffectivePermissionCache(
        principal_id=principal_id,
        resource_id=resource_id,
        roles=roles_out,
        permissions=permissions,
        computed_at=computed_at,
        generation=generation,
        principal_generation=principal_generation,
    )


async def explain_permission(
    session: AsyncSession,
    principal_id: str,
    resource_id: str,
    permission: str,
    access_type: str,
) -> dict:
    """Why a principal has - or hasn't - a permission on a resource (P96-S2,
    ADR 0253 D1): the same walk as `_collect_effective_roles`, but every step
    is reported - the node, its `inherit` flag, each matching assignment with
    where it comes from (direct, a group by name, "everyone") and whether its
    role grants the permission, the scope locks active there - plus the
    verdict and the step that decided it. Computed fresh, never from or into
    the cache; the cached answer is reported next to it, so a stale entry
    shows up as a mismatch."""
    group_rows = await session.execute(
        select(Group.id, Group.name)
        .join(GroupMembership, GroupMembership.group_id == Group.id)
        .where(GroupMembership.principal_id == principal_id)
    )
    groups = {group_id: name for group_id, name in group_rows.all()}
    now = datetime.now(UTC)
    steps: list[dict] = []
    granted_at: str | None = None
    all_permissions: set[str] = set()
    locks: list[dict] = []
    registered = False
    current_id: str | None = resource_id
    stopped_by_inherit = False
    while current_id is not None:
        node = await session.get(ResourceNode, current_id)
        if node is None:
            steps.append({"resource_id": current_id, "registered": False})
            break
        if current_id == resource_id:
            registered = True
        rows = await session.execute(
            select(RoleAssignment, Role)
            .join(Role, Role.id == RoleAssignment.role_id)
            .where(RoleAssignment.resource_id == current_id)
            .where(
                or_(
                    RoleAssignment.principal_id == principal_id,
                    and_(
                        RoleAssignment.principal_type == EVERYONE_PRINCIPAL_TYPE,
                        RoleAssignment.principal_id == EVERYONE_PRINCIPAL_ID,
                    ),
                    and_(
                        RoleAssignment.principal_type == "group",
                        RoleAssignment.principal_id.in_(list(groups) or [""]),
                    ),
                )
            )
        )
        matched = []
        for assignment, role in rows.all():
            if assignment.principal_id == principal_id and assignment.principal_type != "group":
                source = "direct"
            elif assignment.principal_id == EVERYONE_PRINCIPAL_ID:
                source = "everyone"
            elif assignment.principal_id in groups:
                source = f"group:{groups[assignment.principal_id]}"
            else:
                source = "direct"
            grants = permission in role.permissions
            if grants and granted_at is None:
                granted_at = current_id
            all_permissions.update(role.permissions)
            matched.append(
                {
                    "assignment_id": assignment.id,
                    "source": source,
                    "role": role.name,
                    "grants_permission": grants,
                }
            )
        step_locks = []
        lock_rows = await session.execute(
            select(ScopeLock).where(ScopeLock.resource_id == current_id)
        )
        for lock in lock_rows.scalars().all():
            if _scope_lock_is_active(lock, now):
                entry = {
                    "lock_id": lock.id,
                    "blocks_read": lock.blocks_read,
                    "reason": lock.reason,
                    "expires_at": lock.expires_at.isoformat() if lock.expires_at else None,
                }
                step_locks.append(entry)
                locks.append(entry)
        steps.append(
            {
                "resource_id": current_id,
                "resource_type": node.resource_type,
                "registered": True,
                "inherit": node.inherit,
                "assignments": matched,
                "active_locks": step_locks,
            }
        )
        if not node.inherit:
            stopped_by_inherit = current_id != ROOT_RESOURCE_ID and node.parent_id is not None
            break
        current_id = node.parent_id

    blocking = [lock for lock in locks if access_type == "write" or lock["blocks_read"]]
    bypass = "scope_lock.bypass" in all_permissions
    if not registered:
        verdict, reason = (
            False,
            "Ressource ist nicht registriert - ohne Knoten wird alles verweigert.",
        )
    elif blocking and not bypass:
        verdict, reason = False, f"Gesperrt durch Bereichssperre {blocking[0]['lock_id']}."
    elif granted_at is not None:
        verdict, reason = True, f"Gewährt durch eine Zuweisung am Knoten {granted_at}."
    elif stopped_by_inherit:
        verdict, reason = (
            False,
            ("Keine passende Zuweisung; die Vererbung endet an einem Knoten mit inherit=false."),
        )
    else:
        verdict, reason = False, "Keine Zuweisung entlang des Pfads gewährt diese Berechtigung."
    cached = await session.get(
        EffectivePermissionCache, (principal_id, resource_id), populate_existing=True
    )
    generation, principal_generation = await _cache_generations(session, principal_id)
    cache_valid = (
        cached is not None
        and cached.generation == generation
        and cached.principal_generation == principal_generation
    )
    return {
        "principal_id": principal_id,
        "resource_id": resource_id,
        "permission": permission,
        "access_type": access_type,
        "allowed": verdict,
        "reason": reason,
        "groups": sorted(groups.values()),
        "steps": steps,
        "cached": (
            {
                "permissions": cached.permissions,
                "agrees": (permission in cached.permissions) == (granted_at is not None),
            }
            if cache_valid
            else None
        ),
    }


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


async def get_effective_permissions_batch(
    session: AsyncSession, principal_id: str, resource_ids: list[str]
) -> dict[str, EffectivePermissionCache]:
    """The batch form of `get_effective_permissions` (P91-S4, ADR 0259):
    one query for every cached row of `principal_id` among `resource_ids`,
    individual computation only for the misses. Listings filter hundreds of
    children per request; one lookup per child made a root listing of ~800
    folders take a second."""
    if not resource_ids:
        return {}
    generation, principal_generation = await _cache_generations(session, principal_id)
    result = await session.execute(
        select(EffectivePermissionCache)
        .where(
            EffectivePermissionCache.principal_id == principal_id,
            EffectivePermissionCache.resource_id.in_(resource_ids),
            EffectivePermissionCache.generation == generation,
            EffectivePermissionCache.principal_generation == principal_generation,
        )
        .execution_options(populate_existing=True)
    )
    entries = {entry.resource_id: entry for entry in result.scalars().all()}
    for _ in entries:
        metrics.count_lookup(hit=True)
    misses = [resource_id for resource_id in resource_ids if resource_id not in entries]
    if misses:
        entries.update(
            await _compute_batch(session, principal_id, misses, generation, principal_generation)
        )
    return entries


async def _collect_effective_roles_many(
    session: AsyncSession, principal_id: str, resource_ids: list[str]
) -> dict[str, tuple[list[Role], bool]]:
    """`_collect_effective_roles` for many resources at once (P95-S1d, ADR
    0263) - same rules (own assignments, then upward while `inherit`, the
    principal's groups and "everyone"), but every node's contribution is
    computed once and shared: siblings in a listing share their parent's
    whole ancestor chain. Before, a cold listing of 1,000 children walked
    the same chain 1,000 times (3.7 s in P95-S1)."""
    member_group_ids = await _group_ids_for_principal(session, principal_id)
    principal_match = [
        RoleAssignment.principal_id == principal_id,
        and_(
            RoleAssignment.principal_type == EVERYONE_PRINCIPAL_TYPE,
            RoleAssignment.principal_id == EVERYONE_PRINCIPAL_ID,
        ),
    ]
    if member_group_ids:
        principal_match.append(
            and_(
                RoleAssignment.principal_type == "group",
                RoleAssignment.principal_id.in_(member_group_ids),
            )
        )
    nodes: dict[str, ResourceNode | None] = {}
    own_role_ids: dict[str, set[int]] = {}

    async def load(node_ids: list[str]) -> None:
        wanted = [n for n in node_ids if n not in nodes]
        if not wanted:
            return
        found = await session.execute(
            select(ResourceNode).where(ResourceNode.resource_id.in_(wanted))
        )
        for node in found.scalars().all():
            nodes[node.resource_id] = node
        for node_id in wanted:
            nodes.setdefault(node_id, None)
            own_role_ids.setdefault(node_id, set())
        assignments = await session.execute(
            select(RoleAssignment.resource_id, RoleAssignment.role_id).where(
                RoleAssignment.resource_id.in_(wanted), or_(*principal_match)
            )
        )
        for node_id, role_id in assignments.all():
            own_role_ids[node_id].add(role_id)

    # Level by level: the targets, then the parents of those that inherit,
    # and so on - one query pair per level, not per node.
    level = list(dict.fromkeys(resource_ids))
    while level:
        await load(level)
        level = list(
            dict.fromkeys(
                node.parent_id
                for node_id in level
                if (node := nodes.get(node_id)) is not None
                and node.inherit
                and node.parent_id is not None
                and node.parent_id not in nodes
            )
        )

    chain_roles: dict[str, frozenset[int]] = {}

    def roles_from(node_id: str) -> frozenset[int]:
        # A node's own roles plus, while it inherits, its parent's chain.
        # Iterative, so a deep tree can't hit the recursion limit.
        path: list[str] = []
        current: str | None = node_id
        while current is not None and current not in chain_roles:
            node = nodes.get(current)
            if node is None:
                chain_roles[current] = frozenset()
                break
            path.append(current)
            current = node.parent_id if node.inherit else None
        inherited = chain_roles.get(current, frozenset()) if current is not None else frozenset()
        for step in reversed(path):
            inherited = frozenset(own_role_ids.get(step, set())) | inherited
            chain_roles[step] = inherited
        return chain_roles.get(node_id, frozenset())

    per_resource = {resource_id: roles_from(resource_id) for resource_id in resource_ids}
    all_role_ids = set().union(*per_resource.values()) if per_resource else set()
    roles_by_id: dict[int, Role] = {}
    if all_role_ids:
        found = await session.execute(select(Role).where(Role.id.in_(all_role_ids)))
        roles_by_id = {role.id: role for role in found.scalars().all()}
    return {
        resource_id: (
            [roles_by_id[r] for r in role_ids if r in roles_by_id],
            nodes.get(resource_id) is not None,
        )
        for resource_id, role_ids in per_resource.items()
    }


async def _compute_batch(
    session: AsyncSession,
    principal_id: str,
    resource_ids: list[str],
    generation: int,
    principal_generation: int,
) -> dict[str, EffectivePermissionCache]:
    """Computes the misses of a batch together and caches the registered
    ones in one upsert, under the generations read before anything else
    (ADR 0239's rule, unchanged). Unregistered resources aren't cached
    (ADR 0254)."""
    started = time.monotonic() if metrics.rebuild_timing_active() else None
    computed_at = datetime.now(UTC)
    computed: dict[str, EffectivePermissionCache] = {}
    rows = []
    for resource_id, (roles, registered) in (
        await _collect_effective_roles_many(session, principal_id, resource_ids)
    ).items():
        metrics.count_lookup(hit=False)
        entry = EffectivePermissionCache(
            principal_id=principal_id,
            resource_id=resource_id,
            roles=sorted(role.name for role in roles),
            permissions=sorted({p for role in roles for p in role.permissions}),
            computed_at=computed_at,
            generation=generation,
            principal_generation=principal_generation,
        )
        computed[resource_id] = entry
        if registered:
            rows.append(
                {
                    "principal_id": principal_id,
                    "resource_id": resource_id,
                    "roles": entry.roles,
                    "permissions": entry.permissions,
                    "computed_at": computed_at,
                    "generation": generation,
                    "principal_generation": principal_generation,
                }
            )
    # Chunked: asyncpg allows at most 32,767 bind parameters per statement.
    for start in range(0, len(rows), 1000):
        insert_stmt = pg_insert(EffectivePermissionCache).values(rows[start : start + 1000])
        await session.execute(
            insert_stmt.on_conflict_do_update(
                index_elements=["principal_id", "resource_id"],
                set_={
                    "roles": insert_stmt.excluded.roles,
                    "permissions": insert_stmt.excluded.permissions,
                    "computed_at": insert_stmt.excluded.computed_at,
                    "generation": insert_stmt.excluded.generation,
                    "principal_generation": insert_stmt.excluded.principal_generation,
                },
                where=tuple_(
                    EffectivePermissionCache.generation,
                    EffectivePermissionCache.principal_generation,
                )
                < tuple_(
                    insert_stmt.excluded.generation, insert_stmt.excluded.principal_generation
                ),
            )
        )
    if rows:
        await session.flush()
    if started is not None and resource_ids:
        metrics.observe_rebuild((time.monotonic() - started) / len(resource_ids))
    return computed


async def list_active_scope_locks(session: AsyncSession) -> list[ScopeLock]:
    """Every currently active scope lock in the installation - usually
    none, which lets a batch check skip the per-resource ancestor walk."""
    now = datetime.now(UTC)
    result = await session.execute(select(ScopeLock).where(ScopeLock.released_at.is_(None)))
    return [lock for lock in result.scalars().all() if _scope_lock_is_active(lock, now)]


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


_UNSET = object()


class UnknownRoleError(Exception):
    pass


async def set_approval_config(
    session: AsyncSession,
    action_type: str,
    *,
    requires_approval: bool,
    required_permission=_UNSET,
    recipient_role=_UNSET,
) -> ApprovalActionConfig:
    """Upsert. `required_permission`/`recipient_role` are only changed when
    passed (P90-S3) - a caller that only toggles `requires_approval` no longer
    clears them. A recipient role must exist (`UnknownRoleError`)."""
    if recipient_role is not _UNSET and recipient_role is not None:
        exists = await session.scalar(select(Role.id).where(Role.name == recipient_role))
        if exists is None:
            raise UnknownRoleError(f"Rolle {recipient_role!r} existiert nicht")
    config = await session.get(ApprovalActionConfig, action_type)
    if config is None:
        config = ApprovalActionConfig(action_type=action_type, requires_approval=requires_approval)
        session.add(config)
    config.requires_approval = requires_approval
    if required_permission is not _UNSET:
        config.required_permission = required_permission
    if recipient_role is not _UNSET:
        config.recipient_role = recipient_role
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


# Notification is a heads-up, not a broadcast: a role granted to hundreds of
# people (or to "everyone") is not a sensible recipient list.
APPROVAL_RECIPIENT_LIMIT = 50
APPROVAL_NOTIFY_PERMISSION = "approval.notify"


async def _principals_holding_roles(session: AsyncSession, role_ids: set[int]) -> set[str]:
    """User principals holding one of `role_ids` at the root resource -
    directly or through a real group's membership. The implicit "everyone"
    group is skipped (it would mean every account), as are service
    principals (machines don't read notifications)."""
    if not role_ids:
        return set()
    rows = (
        await session.execute(
            select(RoleAssignment).where(
                RoleAssignment.role_id.in_(role_ids),
                RoleAssignment.resource_id == ROOT_RESOURCE_ID,
            )
        )
    ).scalars()
    principals: set[str] = set()
    group_ids: set[str] = set()
    for assignment in rows:
        if assignment.principal_type == "user":
            principals.add(assignment.principal_id)
        elif assignment.principal_type == "group":
            if assignment.principal_id != EVERYONE_PRINCIPAL_ID:
                group_ids.add(assignment.principal_id)
    if group_ids:
        members = await session.execute(
            select(GroupMembership.principal_id).where(GroupMembership.group_id.in_(group_ids))
        )
        principals.update(members.scalars())
    return principals


async def resolve_approval_recipients(
    session: AsyncSession, action_type: str, *, initiated_by: str | None = None
) -> tuple[str, list[str], bool]:
    """Who is told about a new request of `action_type` (P90-S3, ADR 0247):
    (source, sorted principal ids, truncated). In order:
    1. the action type's `recipient_role`;
    2. the holders of its `required_permission` (exactly who may decide);
    3. the holders of `approval.notify` (the "Freigabe-Benachrichtigung"
       role) - for the common "any second person" case.
    The initiator is never among them: they can't approve their own request."""
    config = await get_approval_config(session, action_type)
    roles = list((await session.execute(select(Role))).scalars())
    if config.recipient_role:
        source = "recipient_role"
        role_ids = {r.id for r in roles if r.name == config.recipient_role}
    elif config.required_permission:
        source = "required_permission"
        role_ids = {r.id for r in roles if config.required_permission in (r.permissions or [])}
    else:
        source = "approval_notify"
        role_ids = {r.id for r in roles if APPROVAL_NOTIFY_PERMISSION in (r.permissions or [])}
    principals = await _principals_holding_roles(session, role_ids)
    principals.discard(initiated_by or "")
    ordered = sorted(principals)
    return source, ordered[:APPROVAL_RECIPIENT_LIMIT], len(ordered) > APPROVAL_RECIPIENT_LIMIT


async def require_capability(
    session: AsyncSession, principal_id: str, permission: str, *, is_superuser: bool = False
) -> None:
    """Direct baseline permission check at the root resource - unlike
    `_require_permission_if_configured` below (which only applies *in the
    context of an approval request*), also usable for endpoints without
    any four-eyes involvement (e.g. `POST /maintenance-mode/trigger`, when
    `requires_approval=False`, 4.8/P6-S6).

    `is_superuser` (P63-S1): the caller (`main.py`) has already resolved
    whether `principal_id` is the currently active break-glass superuser
    via `AuthServiceClient.get_active_superuser()` - an HTTP round trip
    that has no place in this DB-only repository module, same separation
    every other call site in this file already keeps. Defaults `False` so
    every pre-existing caller that doesn't pass it keeps its previous,
    unbypassable behavior (only `_require_permission_if_configured`'s
    four-eyes-initiation-eligibility path deliberately does NOT pass this -
    see its own docstring for why)."""
    if is_superuser:
        return
    entry = await get_effective_permissions(session, principal_id, ROOT_RESOURCE_ID)
    if permission not in entry.permissions:
        raise MissingRequiredPermissionError(
            f"{principal_id!r} hält nicht die erforderliche Capability {permission!r}"
        )


async def _require_permission_if_configured(
    session: AsyncSession, config: ApprovalActionConfig, principal_id: str
) -> None:
    """P63-S1: deliberately does NOT pass `is_superuser` through to
    `require_capability` - this gates who may INITIATE a four-eyes-
    protected action, and the project's own established convention
    (`query-service`'s critical-action four-eyes, concept 6.1 item 4) is
    that an activated superuser does not bypass four-eyes protections,
    only the ordinary permission model outside of them. Bypassing
    initiation eligibility here would let a superuser start an approval
    flow they'd otherwise need a real role for, undermining that
    invariant - the general bypass added this session is scoped to
    `require_capability`'s other, non-four-eyes callers only."""
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
    # P85-S2 (ADR 0240): rejecting needs the same entitlement as approving
    # (before, anyone could reject any request). The initiator may always
    # withdraw their own request.
    if rejected_by != request.initiated_by:
        config = await get_approval_config(session, request.action_type)
        await _require_permission_if_configured(session, config, rejected_by)
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
    scope_case_resource_ids: list[str] | None = None,
    grant_kind: str | None = None,
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
        scope_case_resource_ids=scope_case_resource_ids,
        created_at=datetime.now(UTC),
        grant_kind=grant_kind,
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
    case_resource_id: str | None = None,
) -> bool:
    """A set scope list restricts to exactly these IDs; an empty/``None``
    list means "unrestricted on this dimension". If the corresponding ID of
    the operation being checked is not supplied (the caller doesn't know
    it), a set scope list counts as NOT satisfied (fail closed) - see
    main.py ``GET /delegations/check``. `case_resource_id` (Post-Roadmap
    Phase 39 Session 4, ADR 0154) - the fourth dimension, same fail-closed
    reasoning as the other three."""
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
    if delegation.scope_case_resource_ids:
        if case_resource_id is None or case_resource_id not in delegation.scope_case_resource_ids:
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
    case_resource_id: str | None = None,
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
            case_resource_id=case_resource_id,
        )
        for d in delegations
    )


async def create_org_hierarchy_grant(
    session: AsyncSession,
    *,
    principal_id: str,
    grant_kind: str,
    process_definition_id: int,
    ends_at: datetime,
    case_resource_id: str | None = None,
) -> list[Delegation]:
    """The org-hierarchy foundation (P31-S9's `SupervisorAssignment`/reused
    `Group`) put to use for dynamic access grants (Post-Roadmap Phase 31
    Session 10, ADR 0121). Resolves the deputy SET for `grant_kind` and
    creates one `Delegation` row per resolved deputy via the existing
    `create_delegation` - `principal_id` becomes the delegator, each
    resolved person the deputy, exactly Delegation's existing self-service
    shape (ADR 0048), just auto-derived from org-hierarchy data instead of
    a person picking one deputy by hand. `scope_process_definition_ids`
    is set to exactly `[process_definition_id]` (the only delegation-scope
    dimension `workflow-service`'s check actually evaluates) - unrestricted
    on the other two dimensions, same as a self-service delegation left at
    its defaults.

    Deliberately no error for an empty resolved deputy set (e.g. a
    principal with no configured supervisor, no `is_org_unit`-flagged group
    membership, or no group membership at all) - a graceful zero-delegations
    result, not a failure, same posture as `get_supervisor_chain` for a
    principal with none. Since Post-Roadmap Phase 35 Session 1 (ADR 0143),
    `"org_unit"` resolves to only the principal's `is_org_unit=True` group
    membership(s) - previously EVERY group the principal belonged to,
    unioned (an explicitly acknowledged pragmatic stand-in, ADR 0120/0121);
    a principal in no flagged group now yields an empty set rather than
    silently granting through an unrelated group. Each created `Delegation`
    row is stamped with `grant_kind` for admin traceability (previously
    indistinguishable from a self-service delegation, ADR 0121
    "Consequences").

    `case_resource_id` (Post-Roadmap Phase 39 Session 4, ADR 0154):
    optional, when the caller (workflow-service) has already resolved the
    triggering instance's `business_key` to a real case. When given, the
    grant is scoped to BOTH `scope_process_definition_ids` AND
    `scope_case_resource_ids` (both dimensions must match, strictly
    narrower than the process-definition family alone) - `None` preserves
    the exact pre-existing behavior (process-definition-family-wide),
    e.g. when the instance has no resolvable case."""
    if grant_kind == "supervisor":
        assignments = await list_supervisor_assignments(session, principal_id=principal_id)
        deputy_ids = {a.supervisor_principal_id for a in assignments}
    elif grant_kind == "supervisor_chain":
        deputy_ids = await get_supervisor_chain(session, principal_id)
    elif grant_kind == "org_unit":
        deputy_ids = set()
        for group_id in await _org_unit_group_ids_for_principal(session, principal_id):
            members = await list_group_members(session, group_id)
            deputy_ids.update(m.principal_id for m in members)
        deputy_ids.discard(principal_id)
    else:
        raise ValueError(f"unbekannter grant_kind {grant_kind!r}")

    now = datetime.now(UTC)
    delegations: list[Delegation] = []
    for deputy_id in deputy_ids:
        delegations.append(
            await create_delegation(
                session,
                delegator_principal_id=principal_id,
                deputy_principal_id=deputy_id,
                starts_at=now,
                ends_at=ends_at,
                scope_object_type_ids=None,
                scope_process_definition_ids=[process_definition_id],
                scope_folder_resource_ids=None,
                scope_case_resource_ids=[case_resource_id] if case_resource_id else None,
                grant_kind=grant_kind,
            )
        )
    return delegations


# --- Lane bindings (P93-S1, ADR 0249) ----------------------------------------


async def principal_memberships(session: AsyncSession, principal_id: str) -> dict:
    """The groups a principal belongs to and the roles it holds - directly or
    through one of its groups (including the implicit "everyone" group), on
    any resource. What workflow-service's lane eligibility checks against."""
    rows = await session.execute(
        select(Group)
        .join(GroupMembership, GroupMembership.group_id == Group.id)
        .where(GroupMembership.principal_id == principal_id)
        .order_by(Group.name)
    )
    groups = list(rows.scalars())
    group_ids = [g.id for g in groups] + [EVERYONE_PRINCIPAL_ID]
    role_rows = await session.execute(
        select(Role.name)
        .join(RoleAssignment, RoleAssignment.role_id == Role.id)
        .where(
            or_(
                (RoleAssignment.principal_type == "user")
                & (RoleAssignment.principal_id == principal_id),
                (RoleAssignment.principal_type == "group")
                & (RoleAssignment.principal_id.in_(group_ids)),
            )
        )
        .distinct()
    )
    return {
        "principal_id": principal_id,
        "groups": [{"id": g.id, "name": g.name, "is_org_unit": g.is_org_unit} for g in groups],
        "roles": sorted(role_rows.scalars()),
    }


class UnknownBindingError(Exception):
    pass


async def lane_members(
    session: AsyncSession, *, group: str | None, role: str | None, limit: int
) -> list[str]:
    """Principal ids behind a lane binding: a group's members, or a role's
    holders (direct user assignments and members of assigned groups). The
    implicit "everyone" group has no member list - a role held through it
    yields its explicit holders only."""
    if group is not None:
        found = await session.execute(select(Group).where(Group.name == group))
        target = found.scalar_one_or_none()
        if target is None:
            raise UnknownBindingError(f"Gruppe {group!r} unbekannt")
        members = await session.execute(
            select(GroupMembership.principal_id)
            .where(GroupMembership.group_id == target.id)
            .order_by(GroupMembership.principal_id)
            .limit(limit)
        )
        return list(members.scalars())
    found_role = await session.execute(select(Role).where(Role.name == role))
    target_role = found_role.scalar_one_or_none()
    if target_role is None:
        raise UnknownBindingError(f"Rolle {role!r} unbekannt")
    assignments = await session.execute(
        select(RoleAssignment.principal_type, RoleAssignment.principal_id).where(
            RoleAssignment.role_id == target_role.id
        )
    )
    principals: set[str] = set()
    group_ids: set[str] = set()
    for principal_type, principal in assignments.all():
        if principal_type == "user":
            principals.add(principal)
        elif principal != EVERYONE_PRINCIPAL_ID:
            group_ids.add(principal)
    if group_ids:
        members = await session.execute(
            select(GroupMembership.principal_id).where(GroupMembership.group_id.in_(group_ids))
        )
        principals.update(members.scalars())
    return sorted(principals)[:limit]
