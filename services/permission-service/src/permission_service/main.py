import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from permission_service import approval_consumer, repository, structure_consumer
from permission_service.auth_client import AuthServiceClient
from permission_service.models import ApprovalActionConfig, ApprovalRequest, Base, ResourceNode
from permission_service.schemas import (
    ApprovalActionConfigOut,
    ApprovalActionConfigUpdate,
    ApprovalDecision,
    ApprovalRejection,
    ApprovalRequestCreate,
    ApprovalRequestOut,
    BatchCheckRequest,
    BatchCheckResult,
    CheckResult,
    DelegationCheckResult,
    DelegationCreate,
    DelegationOut,
    EffectivePermissionsOut,
    MaintenanceModeActionResult,
    MaintenanceModeLift,
    MaintenanceModeOut,
    MaintenanceModeTrigger,
    ResourceNodeOut,
    ResourceNodeUpdate,
    RoleAssignmentActionResult,
    RoleAssignmentCreate,
    RoleAssignmentOut,
    RoleCreate,
    RoleOut,
    RoleUpdate,
    ScopeLockActionResult,
    ScopeLockCreate,
    ScopeLockOut,
    ScopeLockRelease,
)
from permission_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS permission"))
        await conn.run_sync(Base.metadata.create_all)
        # Ad-hoc-Schema-Erweiterung (kein Alembic, siehe CONTRIBUTING.md):
        # `required_permission` kam erst in P6-S5 dazu, `create_all` legt nur
        # fehlende Tabellen an, keine fehlenden Spalten auf bestehenden.
        await conn.execute(
            text(
                "ALTER TABLE permission.approval_action_config "
                "ADD COLUMN IF NOT EXISTS required_permission VARCHAR(128)"
            )
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    async with app.state.session_factory() as session:
        await repository.ensure_root_resource(session)
        await repository.ensure_domain_admin_roles(session)
        await repository.ensure_everyone_role(session)
        # Break-Glass-Aktivierung (4.6) verlangt zwingend Vier-Augen mit
        # Gruppenbindung - anders als Force-Unlock/Bereichssperren (P6-S4)
        # bewusst schon beim Seeding als `requires_approval=True` mit
        # `required_permission` vorbelegt, nicht erst per Admin-Opt-in. Nur
        # beim allerersten Start gesetzt (idempotent wie die Rollen oben) -
        # ein Betreiber könnte diese Zeile später bewusst über
        # `PUT /approval-config/auth.superuser.activate` ändern, ohne dass
        # ein Neustart das wieder überschreibt.
        if await session.get(ApprovalActionConfig, "auth.superuser.activate") is None:
            await repository.set_approval_config(
                session,
                "auth.superuser.activate",
                requires_approval=True,
                required_permission="breakglass.approve",
            )
        # Not-Shutdown (4.8, P6-S6): anders als Break-Glass NICHT hart auf
        # requires_approval=True vorbelegt ("optional konfigurierbar je
        # Installation") - required_permission ist trotzdem schon gesetzt,
        # damit eine spätere Aktivierung von requires_approval sofort korrekt
        # rollengebunden ist, ohne dass ein Betreiber daran denken muss.
        if await session.get(ApprovalActionConfig, "system.not_shutdown.trigger") is None:
            await repository.set_approval_config(
                session,
                "system.not_shutdown.trigger",
                requires_approval=False,
                required_permission="system.not_shutdown.trigger",
            )
        await repository.get_or_seed_maintenance_mode(session)
        await session.commit()

    app.state.auth_client = AuthServiceClient(settings.auth_service_base_url)

    event_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await event_bus.connect()
    app.state.event_bus = event_bus
    await structure_consumer.start_consuming(
        event_bus, settings.structure_subjects, app.state.session_factory
    )

    # Eigener Producer-Client für publizierte Ereignisse (Bereichssperren, 4.7/5.3) -
    # getrennt vom obigen reinen Konsumenten-Client (ensure_stream=False), da ein
    # Producer den eigenen Stream anlegen muss (siehe ADR 0001).
    publisher = NatsEventBusClient(settings.nats_url, stream="permission", ensure_stream=True)
    await publisher.connect()
    app.state.publisher = publisher

    # Selbst-Konsum des eigenen Approval-Events (4.3, P6-S4): erst jetzt
    # möglich, weil der Stream "permission" gerade eben durch den obigen
    # publisher-Connect angelegt wurde - der reine Konsumenten-Client
    # (event_bus) kann das Subject also erst ab hier abonnieren.
    await approval_consumer.start_consuming(
        event_bus, settings.approval_subjects, app.state.session_factory, publish_event
    )

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
    await publisher.close()
    await event_bus.close()
    await app.state.auth_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


async def publish_event(event_type: str, payload: dict, actor: str | None = None) -> None:
    event = Event(
        event_type=event_type, service_name=settings.service_name, payload=payload, actor=actor
    )
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


@app.put("/roles/{role_id}", response_model=RoleOut)
async def update_role(
    role_id: int, payload: RoleUpdate, session: AsyncSession = Depends(get_session)
) -> RoleOut:
    """Seit P12-S3 (7.3) - Grundlage für den Konfigurationsimport (`config-
    service`), der eine bereits vorhandene Rolle (Abgleich per `name`)
    aktualisieren statt sie zu duplizieren erwarten muss."""
    try:
        role = await repository.update_role(
            session, role_id, description=payload.description, permissions=payload.permissions
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return role


@app.post("/role-assignments", response_model=RoleAssignmentActionResult, status_code=201)
async def create_role_assignment(
    payload: RoleAssignmentCreate, session: AsyncSession = Depends(get_session)
) -> RoleAssignmentActionResult:
    """Seit P17-S3 optional per generischem Vier-Augen-Mechanismus gegated
    (4.3, `permission.role_assignment.create`) - 14.2 nennt
    "Berechtigungsänderung" wörtlich als sensiblen Aktionstyp für die
    Vier-Augen-Vorbelegung des eGov-Pakets. Per Default (keine Konfiguration)
    bleibt das Verhalten unverändert: sofortige Anlage, gleiches Muster wie
    `document.force_unlock`/`permission.scope_lock.create`."""
    config = await repository.get_approval_config(session, "permission.role_assignment.create")
    if config.requires_approval:
        # `RoleAssignmentCreate` hat kein eigenes "wer beantragt das"-Feld
        # (dieser Endpunkt liest wie `create_scope_lock` bewusst keinen
        # `X-DMS-Principal`-Header, älteres, einfacheres Muster als die
        # neueren Delegation-Endpunkte) - `principal_id` (die Person, die die
        # Rolle erhalten soll) ist der einzige verfügbare Platzhalter für
        # `initiated_by`, identisches Kompromiss-Muster wie dort.
        request = await _request_approval(
            session,
            action_type="permission.role_assignment.create",
            initiated_by=payload.principal_id,
            payload={
                "principal_type": payload.principal_type,
                "principal_id": payload.principal_id,
                "role_id": payload.role_id,
                "resource_id": payload.resource_id,
            },
        )
        return RoleAssignmentActionResult(status="pending_approval", approval_request_id=request.id)

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
    return RoleAssignmentActionResult(status="created", role_assignment=assignment)


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


@app.post("/check/batch", response_model=BatchCheckResult)
async def check_batch(
    payload: BatchCheckRequest, session: AsyncSession = Depends(get_session)
) -> BatchCheckResult:
    """Batch-Form von `/check` (P5-S4, Search Service): prüft mehrere
    Resource-IDs in einem Aufruf statt vieler Einzel-Roundtrips. Wiederholt
    dieselbe Logik wie `/check` je Resource-ID - jeder Aufruf trifft den
    bereits vorhandenen `EffectivePermissionCache`, daher keine teure
    Mehrfachberechnung trotz der Schleife."""
    results: dict[str, bool] = {}
    for resource_id in set(payload.resource_ids):
        entry = await repository.get_effective_permissions(
            session, payload.principal_id, resource_id
        )
        active_locks = await repository.get_active_scope_locks_for_resource(session, resource_id)
        blocking_locks = [
            lock for lock in active_locks if payload.access_type == "write" or lock.blocks_read
        ]
        if blocking_locks and "scope_lock.bypass" not in entry.permissions:
            results[resource_id] = False
            continue
        results[resource_id] = payload.permission in entry.permissions
    await session.commit()
    return BatchCheckResult(results=results)


async def _request_approval(
    session: AsyncSession, *, action_type: str, initiated_by: str, payload: dict
) -> ApprovalRequest:
    """Gemeinsamer Erstellungs-/Publish-Pfad für `POST /approval-requests`
    und jeden gegateten Endpunkt (Scope-Locks hier, `document-service`s
    Force-Unlock via HTTP) - vermeidet doppelte Publish-Logik."""
    try:
        request = await repository.create_approval_request(
            session, action_type=action_type, initiated_by=initiated_by, payload=payload
        )
    except repository.MissingRequiredPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "permission.approval.requested",
        {
            "request_id": request.id,
            "action_type": request.action_type,
            "initiated_by": request.initiated_by,
        },
        actor=request.initiated_by,
    )
    return request


@app.get("/approval-config", response_model=list[ApprovalActionConfigOut])
async def list_approval_config(
    session: AsyncSession = Depends(get_session),
) -> list[ApprovalActionConfigOut]:
    return await repository.list_approval_configs(session)


@app.get("/approval-config/{action_type}", response_model=ApprovalActionConfigOut)
async def get_approval_config(
    action_type: str, session: AsyncSession = Depends(get_session)
) -> ApprovalActionConfigOut:
    return await repository.get_approval_config(session, action_type)


@app.put("/approval-config/{action_type}", response_model=ApprovalActionConfigOut)
async def put_approval_config(
    action_type: str,
    payload: ApprovalActionConfigUpdate,
    session: AsyncSession = Depends(get_session),
) -> ApprovalActionConfigOut:
    config = await repository.set_approval_config(
        session,
        action_type,
        requires_approval=payload.requires_approval,
        required_permission=payload.required_permission,
    )
    await session.commit()
    return config


@app.post("/approval-requests", response_model=ApprovalRequestOut, status_code=201)
async def create_approval_request(
    payload: ApprovalRequestCreate, session: AsyncSession = Depends(get_session)
) -> ApprovalRequestOut:
    return await _request_approval(
        session,
        action_type=payload.action_type,
        initiated_by=payload.initiated_by,
        payload=payload.payload,
    )


@app.get("/approval-requests", response_model=list[ApprovalRequestOut])
async def list_approval_requests(
    status: str | None = None,
    action_type: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[ApprovalRequestOut]:
    return await repository.list_approval_requests(session, status=status, action_type=action_type)


@app.get("/approval-requests/{request_id}", response_model=ApprovalRequestOut)
async def get_approval_request(
    request_id: str, session: AsyncSession = Depends(get_session)
) -> ApprovalRequestOut:
    try:
        return await repository.get_approval_request(session, request_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/approval-requests/{request_id}/approve", response_model=ApprovalRequestOut)
async def approve_approval_request(
    request_id: str, payload: ApprovalDecision, session: AsyncSession = Depends(get_session)
) -> ApprovalRequestOut:
    try:
        request = await repository.approve_request(
            session, request_id, approved_by=payload.approved_by
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.NotInitiatorAllowedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except repository.MissingRequiredPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except repository.ApprovalRequestNotPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "permission.approval.approved",
        {
            "request_id": request.id,
            "action_type": request.action_type,
            "initiated_by": request.initiated_by,
            "approved_by": request.approved_by,
            "payload": request.payload,
        },
        actor=request.approved_by,
    )
    return request


@app.post("/approval-requests/{request_id}/reject", response_model=ApprovalRequestOut)
async def reject_approval_request(
    request_id: str, payload: ApprovalRejection, session: AsyncSession = Depends(get_session)
) -> ApprovalRequestOut:
    try:
        request = await repository.reject_request(
            session, request_id, rejected_by=payload.rejected_by, reason=payload.reason
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.ApprovalRequestNotPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "permission.approval.rejected",
        {
            "request_id": request.id,
            "action_type": request.action_type,
            "initiated_by": request.initiated_by,
            "rejected_by": request.rejected_by,
            "reason": request.reason,
        },
        actor=request.rejected_by,
    )
    return request


@app.post("/scope-locks", response_model=ScopeLockActionResult, status_code=201)
async def create_scope_lock(
    payload: ScopeLockCreate, session: AsyncSession = Depends(get_session)
) -> ScopeLockActionResult:
    config = await repository.get_approval_config(session, "permission.scope_lock.create")
    if config.requires_approval:
        request = await _request_approval(
            session,
            action_type="permission.scope_lock.create",
            initiated_by=payload.locked_by,
            payload={
                "resource_id": payload.resource_id,
                "locked_by": payload.locked_by,
                "reason": payload.reason,
                "blocks_read": payload.blocks_read,
                "expires_at": payload.expires_at.isoformat() if payload.expires_at else None,
            },
        )
        return ScopeLockActionResult(status="pending_approval", approval_request_id=request.id)

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
        actor=lock.locked_by,
    )
    return ScopeLockActionResult(status="created", scope_lock=lock)


@app.delete("/scope-locks/{lock_id}", response_model=ScopeLockActionResult)
async def release_scope_lock(
    lock_id: int, payload: ScopeLockRelease, session: AsyncSession = Depends(get_session)
) -> ScopeLockActionResult:
    config = await repository.get_approval_config(session, "permission.scope_lock.release")
    if config.requires_approval:
        request = await _request_approval(
            session,
            action_type="permission.scope_lock.release",
            initiated_by=payload.released_by,
            payload={"lock_id": lock_id, "released_by": payload.released_by},
        )
        return ScopeLockActionResult(status="pending_approval", approval_request_id=request.id)

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
        actor=lock.released_by,
    )
    return ScopeLockActionResult(status="released", scope_lock=lock)


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


@app.get("/maintenance-mode", response_model=MaintenanceModeOut)
async def get_maintenance_mode(session: AsyncSession = Depends(get_session)) -> MaintenanceModeOut:
    mode = await repository.get_or_seed_maintenance_mode(session)
    await session.commit()
    return mode


@app.post("/maintenance-mode/trigger", response_model=MaintenanceModeActionResult, status_code=201)
async def trigger_maintenance_mode(
    payload: MaintenanceModeTrigger, session: AsyncSession = Depends(get_session)
) -> MaintenanceModeActionResult:
    """Not-Shutdown-Auslösung (4.8, P6-S6): anders als Bereichssperren/Force-
    Unlock (P6-S4) verlangt bereits der *direkte* Pfad (ohne Vier-Augen) eine
    Baseline-Rechteprüfung - "frei konfigurierbar, wer auslösen darf, keine
    fest verdrahtete Rolle" (4.8) ist keine optionale Ergänzung wie bei 4.3,
    sondern von Anfang an verbindlich."""
    try:
        await repository.require_capability(
            session, payload.triggered_by, "system.not_shutdown.trigger"
        )
    except repository.MissingRequiredPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    config = await repository.get_approval_config(session, "system.not_shutdown.trigger")
    if config.requires_approval:
        request = await _request_approval(
            session,
            action_type="system.not_shutdown.trigger",
            initiated_by=payload.triggered_by,
            payload={"triggered_by": payload.triggered_by, "reason": payload.reason},
        )
        return MaintenanceModeActionResult(
            status="pending_approval", approval_request_id=request.id
        )

    mode = await repository.activate_maintenance_mode(
        session, triggered_by=payload.triggered_by, reason=payload.reason
    )
    await session.commit()
    await publish_event(
        "permission.maintenance_mode.activated",
        {"triggered_by": mode.triggered_by, "reason": mode.reason},
        actor=mode.triggered_by,
    )
    return MaintenanceModeActionResult(status="activated", maintenance_mode=mode)


@app.post("/maintenance-mode/lift", response_model=MaintenanceModeOut)
async def lift_maintenance_mode(
    payload: MaintenanceModeLift, session: AsyncSession = Depends(get_session)
) -> MaintenanceModeOut:
    """Nur der aktuell aktive Superuser darf aufheben (4.8) - dessen Identität
    lebt in auth-service (P6-S5), daher der Cross-Service-Check statt einer
    eigenen zweiten Quelle der Wahrheit hier."""
    active, superuser_id = await app.state.auth_client.get_active_superuser()
    if not active or superuser_id != payload.lifted_by:
        raise HTTPException(
            status_code=403,
            detail="Nur der aktuell aktive Superuser darf den Wartungsmodus aufheben",
        )

    mode = await repository.lift_maintenance_mode(session, lifted_by=payload.lifted_by)
    await session.commit()
    await publish_event(
        "permission.maintenance_mode.lifted",
        {"lifted_by": mode.lifted_by},
        actor=mode.lifted_by,
    )
    return mode


# --- Stellvertretung bei Abwesenheit (4.4a, P14-S11) -------------------------


def _has_delegation_admin_role(x_dms_roles: str) -> bool:
    roles = {role.strip() for role in x_dms_roles.split(",") if role.strip()}
    return settings.delegation_revoke_admin_role in roles


@app.post("/delegations", response_model=DelegationOut, status_code=201)
async def create_delegation(
    payload: DelegationCreate,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> DelegationOut:
    """Eine Person hinterlegt selbst eine Stellvertretung (4.4a) - bewusst
    kein Feld für ``delegator_principal_id`` im Request-Body: die vertretene
    Person ist immer die anfragende (``X-DMS-Principal``), niemand kann eine
    Delegation im Namen einer dritten Person anlegen."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    if payload.ends_at <= payload.starts_at:
        raise HTTPException(status_code=400, detail="ends_at muss nach starts_at liegen")

    delegation = await repository.create_delegation(
        session,
        delegator_principal_id=x_dms_principal,
        deputy_principal_id=payload.deputy_principal_id,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        scope_object_type_ids=payload.scope_object_type_ids,
        scope_process_definition_ids=payload.scope_process_definition_ids,
        scope_folder_resource_ids=payload.scope_folder_resource_ids,
    )
    await session.commit()
    await publish_event(
        "permission.delegation.created",
        {"delegation_id": delegation.id, "deputy_principal_id": delegation.deputy_principal_id},
        actor=x_dms_principal,
    )
    return delegation


@app.get("/delegations", response_model=list[DelegationOut])
async def list_delegations(
    delegator_principal_id: str | None = None,
    deputy_principal_id: str | None = None,
    active_only: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[DelegationOut]:
    return await repository.list_delegations(
        session,
        delegator_principal_id=delegator_principal_id,
        deputy_principal_id=deputy_principal_id,
        active_only=active_only,
    )


@app.get("/delegations/active-for-deputy/{principal_id}", response_model=list[DelegationOut])
async def list_active_delegations_for_deputy(
    principal_id: str, session: AsyncSession = Depends(get_session)
) -> list[DelegationOut]:
    """Für wen ``principal_id`` gerade als Stellvertretung aktiv ist (4.4a) -
    von user-ui/reviewer-ui genutzt, um "im Auftrag von"-Auswahllisten zu
    füllen, ohne dass das Frontend selbst über alle Delegationen filtern muss."""
    return await repository.list_delegations(
        session, deputy_principal_id=principal_id, active_only=True
    )


@app.get("/delegations/check", response_model=DelegationCheckResult)
async def check_delegation(
    deputy_principal_id: str,
    delegator_principal_id: str,
    process_definition_id: int | None = None,
    object_type_id: int | None = None,
    folder_resource_id: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> DelegationCheckResult:
    """Eigentlicher Durchsetzungs-Endpunkt (4.4a) - von workflow-service beim
    Aufgabenabschluss "im Auftrag von" aufgerufen (gleiches Muster wie
    document-services `GET /check` für Leserechte, P14-S10)."""
    allowed = await repository.is_active_deputy_for(
        session,
        deputy_principal_id=deputy_principal_id,
        delegator_principal_id=delegator_principal_id,
        process_definition_id=process_definition_id,
        object_type_id=object_type_id,
        folder_resource_id=folder_resource_id,
    )
    return DelegationCheckResult(allowed=allowed)


@app.delete("/delegations/{delegation_id}", status_code=204)
async def revoke_delegation(
    delegation_id: str,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Vorzeitige Beendigung (4.4a) - durch die vertretene Person selbst oder
    eine berechtigte Admin-Rolle (Konzept-Wortlaut), NICHT durch die
    Stellvertretung selbst (die könnte sich sonst eine fremde Delegation
    einseitig verlängert vorstellen, indem sie die eigene Widerrufsmöglichkeit
    ignoriert - der Widerruf muss von der Seite kommen können, die die
    Verantwortung trägt)."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")

    delegation = await repository.get_delegation(session, delegation_id)
    if delegation is None:
        raise HTTPException(status_code=404, detail=f"Delegation {delegation_id!r} unbekannt")
    if delegation.delegator_principal_id != x_dms_principal and not _has_delegation_admin_role(
        x_dms_roles
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "Nur die vertretene Person oder eine Admin-Rolle darf diese Delegation widerrufen"
            ),
        )

    await repository.revoke_delegation(session, delegation_id, revoked_by=x_dms_principal)
    await session.commit()
    await publish_event(
        "permission.delegation.revoked",
        {"delegation_id": delegation_id},
        actor=x_dms_principal,
    )
