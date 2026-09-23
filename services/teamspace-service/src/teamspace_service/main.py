import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import httpx
from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_metrics_client import (
    SensorConfigClient,
    bootstrap_http_sensors,
    http_sensor_declarations,
    metrics_payload,
)
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from teamspace_service import consumer, repository
from teamspace_service.clients import (
    AuthServiceClient,
    FolderServiceClient,
    PermissionServiceClient,
)
from teamspace_service.models import Base, TeamspaceAdGroupBinding, TeamspaceMember
from teamspace_service.schemas import (
    AdGroupMemberPreview,
    TeamspaceAdGroupBindingCreate,
    TeamspaceAdGroupBindingOut,
    TeamspaceAdminOut,
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

_TEAMSPACE_ADMIN_PRINCIPAL_ID = "teamspace-service"
_REQUIRED_ROLE_NAMES = ("domain-admin-users",)


async def _require_auth_service_caller(x_dms_principal: str = Header(default="")) -> None:
    """P55-S2: gates `DELETE /principals/{id}/teamspace-memberships` -
    system-to-system cleanup callback, meant only for `auth-service`'s own
    `DELETE /users/{id}` to invoke, never a real end user. Same fixed
    system-identity convention as P54-S2/ADR 0173's
    `_require_workflow_service_caller` in `migration-service`."""
    if x_dms_principal != "auth-service":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Nur auth-service darf diesen Endpunkt aufrufen",
        )


async def _ensure_bootstrap_permissions() -> None:
    """Since P32-S1 (ADR 0130): `PermissionServiceClient._ensure_role`'s
    get-or-create call to `POST /roles` is now self-gated
    (`admin.user_management`) - idempotent self-assignment at startup,
    same bootstrap pattern as `config-service`'s/`migration-service`'s
    `_ensure_bootstrap_permissions`/`_ensure_config_admin_permission`."""
    async with httpx.AsyncClient(
        base_url=settings.permission_service_base_url, timeout=10.0
    ) as client:
        roles = (await client.get("/roles")).json()
        existing_assignments = (
            await client.get(
                "/role-assignments", params={"principal_id": _TEAMSPACE_ADMIN_PRINCIPAL_ID}
            )
        ).json()
        assigned_role_ids = {a["role_id"] for a in existing_assignments}
        for role_name in _REQUIRED_ROLE_NAMES:
            role = next((r for r in roles if r["name"] == role_name), None)
            if role is None:
                logger.warning("teamspace_service_bootstrap_role_missing: %s", role_name)
                continue
            if role["id"] in assigned_role_ids:
                continue
            response = await client.post(
                "/role-assignments",
                json={
                    "principal_type": "service",
                    "principal_id": _TEAMSPACE_ADMIN_PRINCIPAL_ID,
                    "role_id": role["id"],
                    "resource_id": "root",
                },
            )
            response.raise_for_status()


async def _ensure_teamspace_isolation_backfill(session_factory) -> None:
    """Post-Roadmap Phase 38 Session 4 (ADR 0149) - every teamspace
    created BEFORE this session got its resource node the old way (the
    async `folder.resource.created` event, `inherit` defaulting to
    `True`) and therefore still inherits straight up to `root`, where
    `folder-service`/`document-service`'s newly-enforced checks now grant
    "everyone" broad read/write - without this backfill, an existing
    teamspace's isolation would silently NOT apply until its root folder
    happened to be touched by some other code path. Idempotent
    (`ensure_isolated_resource` is create-if-missing + an unconditional
    `inherit=False` set), safe to run on every startup, same pattern as
    `ensure_domain_admin_roles`."""
    async with session_factory() as session:
        root_folder_ids = await repository.list_all_root_folder_ids(session)
    for root_folder_id in root_folder_ids:
        try:
            await app.state.permission_client.ensure_isolated_resource(
                resource_id=root_folder_id, parent_id="root"
            )
        except httpx.HTTPStatusError:
            logger.warning("teamspace_isolation_backfill_failed: root_folder_id=%s", root_folder_id)


async def _reconcile_ad_group_binding(
    session: AsyncSession, binding: TeamspaceAdGroupBinding
) -> None:
    """One binding's worth of reconciliation - factored out of
    `_ad_group_reconciliation_poll_loop` so a single broken binding
    (group deleted in Keycloak, a transient auth-service error) can't
    abort the whole tick, same error-isolation shape as
    `workflow-service`'s `_sla_poll_loop` iterating its own instances."""
    teamspace = await repository.get_teamspace(session, binding.teamspace_id)
    members = await app.state.auth_client.get_group_members(binding.ad_group_name)
    if members is None:
        logger.warning(
            "ad_group_reconciliation_group_missing: teamspace_id=%s ad_group_name=%s",
            binding.teamspace_id,
            binding.ad_group_name,
        )
        return
    actor = f"ad-group:{binding.ad_group_name}"

    existing_from_this_group = await repository.list_members_by_source_group(
        session, binding.teamspace_id, binding.ad_group_name
    )
    existing_principal_ids = {m.principal_id for m in existing_from_this_group}

    for user in members:
        if user["id"] in existing_principal_ids:
            continue
        try:
            await repository.add_member(
                session,
                binding.teamspace_id,
                principal_id=user["id"],
                can_manage_members=False,
                invited_by=actor,
                source_ad_group_name=binding.ad_group_name,
            )
        except repository.DuplicateMemberError:
            # Already a member via a manual invite (or another binding) -
            # leave that row's attribution exactly as it is, see
            # `TeamspaceMember.source_ad_group_name`'s own docstring.
            continue
        await app.state.permission_client.grant_resource_access(
            principal_id=user["id"], resource_id=teamspace.root_folder_id
        )
        await publish_event(
            "teamspace.member_invited",
            subject=binding.teamspace_id,
            payload={"principal_id": user["id"], "source_ad_group_name": binding.ad_group_name},
            actor=actor,
        )

    for member in existing_from_this_group:
        if member.principal_id in {m["id"] for m in members}:
            continue
        await repository.remove_member(session, binding.teamspace_id, member.principal_id)
        await app.state.permission_client.revoke_resource_access(
            principal_id=member.principal_id, resource_id=teamspace.root_folder_id
        )
        await app.state.permission_client.revoke_manager_access(
            principal_id=member.principal_id, resource_id=teamspace.root_folder_id
        )
        await publish_event(
            "teamspace.member_removed",
            subject=binding.teamspace_id,
            payload={
                "principal_id": member.principal_id,
                "source_ad_group_name": binding.ad_group_name,
            },
            actor=actor,
        )


async def _ad_group_reconciliation_poll_loop(session_factory) -> None:
    """Keeps `permission-service` role assignments (via `teamspace_member`
    rows) in sync with LIVE AD/Keycloak group membership for every bound
    teamspace (2.5, Post-Roadmap Phase 74 Session 3, ADR 0160/ADR 0217) -
    grants newly-added group members, revokes departed ones, per binding.
    Deliberately live/poll-reconciled every tick, never a one-time
    snapshot copy - see ADR 0160's own "Rationale" (extends ADR 0093's
    "Keycloak/AD is sole source of truth" principle to this second
    feature area). Same error-isolation/poll idiom as `workflow-service`'s
    `_sla_poll_loop` - no maintenance-mode skip check (this service's
    `PermissionServiceClient` predates the shared `dms_permission_client`
    migration and has no `is_maintenance_active()` of its own, a known,
    pre-existing, already-documented gap elsewhere in this project's own
    maintenance-mode coverage, not attempted here)."""
    while True:
        try:
            async with session_factory() as session:
                bindings = await repository.list_all_ad_group_bindings(session)
                for binding in bindings:
                    try:
                        await _reconcile_ad_group_binding(session, binding)
                    except Exception:
                        logger.exception(
                            "ad_group_reconciliation_binding_failed: teamspace_id=%s "
                            "ad_group_name=%s - wird beim nächsten Tick erneut versucht.",
                            binding.teamspace_id,
                            binding.ad_group_name,
                        )
                await session.commit()
        except Exception:
            logger.exception(
                "AD-Gruppen-Reconciliation-Tick fehlgeschlagen - wird beim nächsten Tick "
                "erneut versucht."
            )
        await asyncio.sleep(settings.ad_group_reconciliation_poll_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS teamspace"))
        await conn.run_sync(Base.metadata.create_all)
        # Ad-hoc schema extension (no Alembic in this early phase, see
        # CONTRIBUTING.md): `create_all` creates missing TABLES (like the
        # new `teamspace_ad_group_binding` above) but doesn't alter an
        # already-existing one - `source_ad_group_name` is new in
        # Post-Roadmap Phase 74 Session 3 (ADR 0160/ADR 0217). Idempotent
        # thanks to IF NOT EXISTS.
        await conn.execute(
            text(
                "ALTER TABLE teamspace.teamspace_member "
                "ADD COLUMN IF NOT EXISTS source_ad_group_name VARCHAR(256)"
            )
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.folder_client = FolderServiceClient(settings.folder_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)
    app.state.auth_client = AuthServiceClient(settings.auth_service_base_url)
    await _ensure_bootstrap_permissions()
    await _ensure_teamspace_isolation_backfill(app.state.session_factory)

    sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
    await sensor_config_client.start()
    sensor_config_proxy.bind(sensor_config_client)
    app.state.sensor_config_client = sensor_config_client
    app.state.sensor_registry = sensor_registry

    event_bus = NatsEventBusClient(settings.nats_url, stream="teamspace")
    await event_bus.connect()
    app.state.event_bus = event_bus

    # P55-S1/ADR 0175: separate consumer-side connection, same dual-bus
    # pattern as case-service/notification-service - reacts to
    # `folder.resource.deleted` to clean up a teamspace whose root folder
    # was deleted directly via folder-service (bypassing this service's
    # own DELETE /teamspaces/{id}).
    consumer_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await consumer_bus.connect()
    app.state.consumer_bus = consumer_bus
    await consumer.start_consuming(
        consumer_bus,
        settings.subjects,
        app.state.session_factory,
        app.state.permission_client,
    )

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
        sensors=http_sensor_declarations(),
    )

    ad_group_reconciliation_poll_task = asyncio.create_task(
        _ad_group_reconciliation_poll_loop(app.state.session_factory)
    )

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    ad_group_reconciliation_poll_task.cancel()
    with suppress(asyncio.CancelledError):
        await ad_group_reconciliation_poll_task
    sensor_config_proxy.unbind()
    await app.state.sensor_config_client.stop()
    if registration:
        await registration.stop()
    await event_bus.close()
    await consumer_bus.close()
    await app.state.folder_client.close()
    await app.state.permission_client.close()
    await app.state.auth_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)

# Sensor concept (10.1, full rollout): must run at module level, right
# after `app` is constructed - see bootstrap_http_sensors's docstring
# for why this can't move into `lifespan` (FastAPI forbids adding
# middleware once the app has started).
sensor_config_proxy, sensor_registry, _http_requests_sensor, _http_duration_sensor = (
    bootstrap_http_sensors(app, settings.service_name)
)


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
    """The actual access regime of this service, independent of the rest
    of the RBAC (4.1) (2.5, P14-S6) - a pure membership check against the
    service's own `teamspace_member` table, no call to
    `permission-service`."""
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


@app.get("/metrics")
def get_metrics() -> Response:
    body, content_type = metrics_payload(app.state.sensor_registry)
    return Response(content=body, media_type=content_type)


@app.post("/teamspaces", response_model=TeamspaceOut, status_code=status.HTTP_201_CREATED)
async def create_teamspace(
    payload: TeamspaceCreate,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> TeamspaceOut:
    """Any authenticated principal may create a new teamspace (concept 2.5:
    "without needing any administrative pre-setup") - deliberately no
    capability gate as with process/DMN definitions."""
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
    await app.state.permission_client.ensure_isolated_resource(
        resource_id=teamspace.root_folder_id, parent_id="root"
    )
    await app.state.permission_client.grant_resource_access(
        principal_id=x_dms_principal, resource_id=teamspace.root_folder_id
    )
    # The creator is always the first member with `can_manage_members=true`
    # (`repository.create_teamspace`) - Phase 44 Session 2/ADR 0163's
    # second role must be granted here too, not just on a later promotion.
    await app.state.permission_client.grant_manager_access(
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


@app.get("/admin/teamspaces", response_model=list[TeamspaceAdminOut])
async def list_all_teamspaces(
    limit: int = 100,
    offset: int = 0,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[TeamspaceAdminOut]:
    """Installation-wide overview (Post-Roadmap Phase 22 Session 5,
    admin UI) - unlike `GET /teamspaces`, NOT filtered by membership,
    hence a real `permission-service` permission check instead of the
    `_require_member`/`_require_manager` check against the service's own
    `teamspace_member` table otherwise used in this service. `limit`/
    `offset` since P62-S1 (previously fully unbounded)."""
    if not x_dms_principal:
        raise HTTPException(status_code=403, detail="X-DMS-Principal fehlt")
    if not await app.state.permission_client.has_permission(
        x_dms_principal, "admin.teamspace_management"
    ):
        raise HTTPException(status_code=403, detail="admin.teamspace_management erforderlich")
    rows = await repository.list_all_teamspaces_with_member_counts(
        session, limit=limit, offset=offset
    )
    return [
        TeamspaceAdminOut(
            id=teamspace.id,
            name=teamspace.name,
            description=teamspace.description,
            root_folder_id=teamspace.root_folder_id,
            created_by=teamspace.created_by,
            created_at=teamspace.created_at,
            updated_at=teamspace.updated_at,
            member_count=member_count,
        )
        for teamspace, member_count in rows
    ]


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
    """Deletes only the teamspace metadata, see
    `repository.delete_teamspace` - the root folder remains. Since Post-
    Roadmap Phase 38 Session 4 (ADR 0149), also restores the kept folder's
    default `inherit`, see `PermissionServiceClient.
    restore_default_inheritance`'s docstring - without it, the folder
    would stay permanently unreachable (`inherit=False` with every
    member's grant just revoked below)."""
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
        # Phase 44 Session 2 (ADR 0163) - same cleanup for the second
        # role, no-op for members who never held it.
        await app.state.permission_client.revoke_manager_access(
            principal_id=member.principal_id, resource_id=teamspace.root_folder_id
        )
    await app.state.permission_client.restore_default_inheritance(
        resource_id=teamspace.root_folder_id
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
    if payload.can_manage_members:
        await app.state.permission_client.grant_manager_access(
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
        teamspace = await repository.get_teamspace(session, teamspace_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        member = await repository.update_member(
            session, teamspace_id, principal_id, can_manage_members=payload.can_manage_members
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Phase 44 Session 2 (ADR 0163) - keep the `folder.delete`-carrying
    # `teamspace-manager` role in sync with `can_manage_members`. Both
    # calls are idempotent (`_grant`/`_revoke`), so no need to diff
    # against the PREVIOUS value first - always granting/revoking
    # according to the new value converges to the same result.
    if payload.can_manage_members:
        await app.state.permission_client.grant_manager_access(
            principal_id=principal_id, resource_id=teamspace.root_folder_id
        )
    else:
        await app.state.permission_client.revoke_manager_access(
            principal_id=principal_id, resource_id=teamspace.root_folder_id
        )
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
    """Removing one's own membership ("leaving a teamspace") is allowed
    for every member; removing OTHER members requires
    `can_manage_members`. Deliberate boundary: no protection against the
    last member with management rights removing themselves, making the
    teamspace unmanageable (see `docs/services/teamspace-service.md`)."""
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
    # Unconditional, safe even for a departing non-manager (Phase 44
    # Session 2/ADR 0163) - `revoke_manager_access` is a no-op when no
    # matching assignment exists.
    await app.state.permission_client.revoke_manager_access(
        principal_id=principal_id, resource_id=teamspace.root_folder_id
    )
    await session.commit()
    await publish_event(
        "teamspace.member_removed",
        subject=teamspace_id,
        payload={"principal_id": principal_id},
        actor=x_dms_principal,
    )


@app.get("/teamspaces/{teamspace_id}/ad-group-preview", response_model=list[AdGroupMemberPreview])
async def preview_ad_group(
    teamspace_id: str,
    ad_group_name: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Preview a group's current membership before binding it (2.5,
    Post-Roadmap Phase 74 Session 3, ADR 0160/ADR 0217) - manager-only,
    same gate as `invite_member`. Deliberately proxies `auth-service`'s
    `GET /groups/{name}/members` rather than exposing that endpoint
    directly to interactive callers: a full group roster is a wider
    disclosure than `GET /users/lookup`'s single-name existence check
    (ADR 0160's own flagged design fork), so it is only ever reachable
    already narrowed to "a manager of a specific teamspace, previewing a
    specific bind action" - never a general, installation-wide directory
    capability."""
    await _require_manager(session, teamspace_id, x_dms_principal)
    members = await app.state.auth_client.get_group_members(ad_group_name)
    if members is None:
        raise HTTPException(status_code=404, detail=f"AD-Gruppe {ad_group_name!r} unbekannt")
    return members


@app.post(
    "/teamspaces/{teamspace_id}/ad-group-bindings",
    response_model=TeamspaceAdGroupBindingOut,
    status_code=status.HTTP_201_CREATED,
)
async def bind_ad_group(
    teamspace_id: str,
    payload: TeamspaceAdGroupBindingCreate,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> TeamspaceAdGroupBindingOut:
    """Binds a teamspace to an AD/Keycloak group for ongoing membership
    synchronization (2.5, Post-Roadmap Phase 74 Session 3, ADR 0160/ADR
    0217) - manager-only, same gate as `invite_member`. Validates the
    group actually exists (`404` otherwise) via the same service-to-
    service call `preview_ad_group` uses, then performs an immediate
    initial sync (not waiting for the next `_ad_group_reconciliation_
    poll_loop` tick) so a manager sees the effect of binding right away."""
    try:
        await repository.get_teamspace(session, teamspace_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_manager(session, teamspace_id, x_dms_principal)
    if await app.state.auth_client.get_group_members(payload.ad_group_name) is None:
        raise HTTPException(
            status_code=404, detail=f"AD-Gruppe {payload.ad_group_name!r} unbekannt"
        )
    try:
        binding = await repository.create_ad_group_binding(
            session, teamspace_id, ad_group_name=payload.ad_group_name, invited_by=x_dms_principal
        )
    except repository.DuplicateBindingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "teamspace.ad_group_bound",
        subject=teamspace_id,
        payload={"ad_group_name": payload.ad_group_name},
        actor=x_dms_principal,
    )
    await _reconcile_ad_group_binding(session, binding)
    await session.commit()
    return binding


@app.get(
    "/teamspaces/{teamspace_id}/ad-group-bindings", response_model=list[TeamspaceAdGroupBindingOut]
)
async def list_ad_group_bindings(
    teamspace_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list:
    await _require_member(session, teamspace_id, x_dms_principal)
    return await repository.list_ad_group_bindings(session, teamspace_id)


@app.delete(
    "/teamspaces/{teamspace_id}/ad-group-bindings/{ad_group_name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unbind_ad_group(
    teamspace_id: str,
    ad_group_name: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Unbinding immediately revokes every membership row this binding
    itself created (`source_ad_group_name == ad_group_name`) rather than
    waiting for the next poll tick to notice the binding is gone - a
    manually-invited member (even one who happens to also be in this AD
    group) is untouched, same attribution boundary as the poll loop's own
    removal logic."""
    try:
        teamspace = await repository.get_teamspace(session, teamspace_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_manager(session, teamspace_id, x_dms_principal)
    try:
        await repository.delete_ad_group_binding(session, teamspace_id, ad_group_name)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    for member in await repository.list_members_by_source_group(
        session, teamspace_id, ad_group_name
    ):
        await repository.remove_member(session, teamspace_id, member.principal_id)
        await app.state.permission_client.revoke_resource_access(
            principal_id=member.principal_id, resource_id=teamspace.root_folder_id
        )
        await app.state.permission_client.revoke_manager_access(
            principal_id=member.principal_id, resource_id=teamspace.root_folder_id
        )
    await session.commit()
    await publish_event(
        "teamspace.ad_group_unbound",
        subject=teamspace_id,
        payload={"ad_group_name": ad_group_name},
        actor=x_dms_principal,
    )


@app.delete(
    "/principals/{principal_id}/teamspace-memberships",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_require_auth_service_caller)],
)
async def delete_principal_memberships(
    principal_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    """P55-S2/auth-service's `DELETE /users/{id}` cross-service cleanup:
    removes `principal_id` from every teamspace they currently belong to
    (not the teamspaces themselves, unlike P55-S1/ADR 0175's orphan
    cleanup - the folders/teamspaces stay intact, only this one deleted
    user's membership and `permission-service` access on each go away).
    Reuses the exact same per-teamspace revoke+remove logic
    `remove_member` above already uses. Idempotent - a principal with no
    memberships is a silent no-op, not a 404."""
    memberships = await repository.list_memberships_for_principal(session, principal_id)
    for membership in memberships:
        try:
            teamspace = await repository.get_teamspace(session, membership.teamspace_id)
        except repository.NotFoundError:
            continue
        await repository.remove_member(session, membership.teamspace_id, principal_id)
        await app.state.permission_client.revoke_resource_access(
            principal_id=principal_id, resource_id=teamspace.root_folder_id
        )
        await app.state.permission_client.revoke_manager_access(
            principal_id=principal_id, resource_id=teamspace.root_folder_id
        )
        await publish_event(
            "teamspace.member_removed",
            subject=membership.teamspace_id,
            payload={"principal_id": principal_id},
            actor="auth-service",
        )
    await session.commit()


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
    """Every member may delete every appointment - deliberately fully
    shared, no creator-exclusive right (see `models.py`)."""
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
