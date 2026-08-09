import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from fleet_management_service import orchestration, repository
from fleet_management_service.agent_client import AgentError, FleetAgentClient
from fleet_management_service.models import Base, InstallationRun, ManagedInstallation, UpdatePlan
from fleet_management_service.schemas import (
    ApprovalDecision,
    GroupCreate,
    GroupMemberAdd,
    GroupOut,
    InstallationRunOut,
    InstallationStatusOut,
    LicenseUploadRequest,
    ManagedInstallationCreate,
    ManagedInstallationCreateOut,
    ManagedInstallationOut,
    MarkDoneRequest,
    ProvisionRequest,
    RolloutCreate,
    RolloutOut,
    RolloutStart,
    UpdatePlanCreate,
    UpdatePlanOut,
)
from fleet_management_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

# Für Tests austauschbar (`app.state.agent_transport = httpx.MockTransport(...)`),
# damit ein Statusabruf/eine Provisionierung ohne echtes Netzwerk gegen einen
# In-Prozess-Stub der Ziel-Installation läuft (gleiches Muster wie
# `federation-hub-service`s `app.state.http_client`-Austausch in Tests).
_AGENT_TRANSPORT_STATE_KEY = "agent_transport"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS fleet"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.agent_transport = None

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


def _agent_client(installation: ManagedInstallation) -> FleetAgentClient:
    return FleetAgentClient(
        gateway_base_url=installation.gateway_base_url,
        fleet_agent_api_key=installation.fleet_agent_api_key,
        timeout=settings.agent_request_timeout_seconds,
        transport=app.state.agent_transport,
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.post(
    "/installations",
    response_model=ManagedInstallationCreateOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_installation(
    payload: ManagedInstallationCreate, session: AsyncSession = Depends(get_session)
) -> ManagedInstallationCreateOut:
    installation, api_key = await repository.create_managed_installation(session, payload)
    await session.commit()
    return ManagedInstallationCreateOut(
        id=installation.id,
        display_name=installation.display_name,
        gateway_base_url=installation.gateway_base_url,
        created_at=installation.created_at,
        updated_at=installation.updated_at,
        fleet_agent_api_key=api_key,
    )


@app.get("/installations", response_model=list[ManagedInstallationOut])
async def list_installations(
    session: AsyncSession = Depends(get_session),
) -> list[ManagedInstallation]:
    """Bewusst ohne `fleet_agent_api_key` im Response-Model - der Klartext-
    Schlüssel wird nur einmal bei der Anlage zurückgegeben (siehe oben)."""
    return await repository.list_managed_installations(session)


@app.delete("/installations/{installation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_installation(
    installation_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    try:
        await repository.delete_managed_installation(session, installation_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()


async def _fetch_status(installation: ManagedInstallation) -> InstallationStatusOut:
    client = _agent_client(installation)
    try:
        identity = await client.get_installation_identity()
        license_status = await client.get_license_status()
        return InstallationStatusOut(
            id=installation.id,
            display_name=installation.display_name,
            reachable=True,
            installation_id=identity.get("id"),
            installation_display_name=identity.get("display_name"),
            license_status=license_status,
        )
    except Exception as exc:  # noqa: BLE001 - eine nicht erreichbare Installation
        # darf die Übersicht der übrigen nicht verhindern (siehe schemas.py).
        logger.warning(
            "fleet_agent_status_unreachable",
            extra={"installation_id": installation.id, "error": str(exc)},
        )
        return InstallationStatusOut(
            id=installation.id,
            display_name=installation.display_name,
            reachable=False,
            error=str(exc),
        )
    finally:
        await client.close()


@app.get("/installations/{installation_id}/status", response_model=InstallationStatusOut)
async def get_installation_status(
    installation_id: str, session: AsyncSession = Depends(get_session)
) -> InstallationStatusOut:
    try:
        installation = await repository.get_managed_installation(session, installation_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await _fetch_status(installation)


@app.get("/installations/status", response_model=list[InstallationStatusOut])
async def list_installation_statuses(
    session: AsyncSession = Depends(get_session),
) -> list[InstallationStatusOut]:
    """3a: "grundlegende Health-Übersicht" über die gesamte Flotte - parallel
    abgefragt (`asyncio.gather`), damit eine einzelne langsame/nicht
    erreichbare Installation die Antwortzeit für die übrigen nicht
    dominiert."""
    installations = await repository.list_managed_installations(session)
    return list(await asyncio.gather(*(_fetch_status(i) for i in installations)))


@app.post("/installations/{installation_id}/license")
async def push_license(
    installation_id: str,
    payload: LicenseUploadRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """3a: "Lizenzvergabe/-verlängerung" - reicht das Lizenztoken über den
    Gateway der Zielinstallation an deren `license-service` weiter, siehe
    `agent_client.FleetAgentClient.upload_license`."""
    try:
        installation = await repository.get_managed_installation(session, installation_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    client = _agent_client(installation)
    try:
        return await client.upload_license(payload.license_token)
    except AgentError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    finally:
        await client.close()


@app.post("/installations/{installation_id}/provision")
async def provision_installation(
    installation_id: str,
    payload: ProvisionRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """3a: "zentrales Provisioning neuer Installationen aus einer
    Konfigurationsvorlage heraus" - `payload.config_document` ist ein
    reguläres 7.3-Konfigurationsdokument (z. B. der Export einer
    Referenzinstallation, oder künftig ein kuratiertes Paket aus Phase 17),
    dieser Service kuratiert selbst keine Vorlagen-Bibliothek."""
    try:
        installation = await repository.get_managed_installation(session, installation_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    client = _agent_client(installation)
    try:
        return await client.provision_config(payload.config_document, categories=payload.categories)
    except AgentError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    finally:
        await client.close()


# --- Flotten-Update-Orchestrierung (3a-Erweiterung, P13-S2b) ----------------


async def _group_out(session: AsyncSession, group) -> GroupOut:
    member_ids = await repository.list_group_member_ids(session, group.id)
    return GroupOut(
        id=group.id, name=group.name, created_at=group.created_at, installation_ids=member_ids
    )


@app.post("/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(
    payload: GroupCreate, session: AsyncSession = Depends(get_session)
) -> GroupOut:
    group = await repository.create_group(session, payload.name)
    await session.commit()
    return await _group_out(session, group)


@app.get("/groups", response_model=list[GroupOut])
async def list_groups(session: AsyncSession = Depends(get_session)) -> list[GroupOut]:
    groups = await repository.list_groups(session)
    return [await _group_out(session, g) for g in groups]


@app.post("/groups/{group_id}/members", response_model=GroupOut)
async def add_group_member(
    group_id: str, payload: GroupMemberAdd, session: AsyncSession = Depends(get_session)
) -> GroupOut:
    try:
        await repository.add_group_member(session, group_id, payload.installation_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    group = await repository.get_group(session, group_id)
    await session.commit()
    return await _group_out(session, group)


@app.delete("/groups/{group_id}/members/{installation_id}", response_model=GroupOut)
async def remove_group_member(
    group_id: str, installation_id: str, session: AsyncSession = Depends(get_session)
) -> GroupOut:
    try:
        await repository.remove_group_member(session, group_id, installation_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    group = await repository.get_group(session, group_id)
    await session.commit()
    return await _group_out(session, group)


@app.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(group_id: str, session: AsyncSession = Depends(get_session)) -> None:
    try:
        await repository.delete_group(session, group_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()


@app.post("/plans", response_model=UpdatePlanOut, status_code=status.HTTP_201_CREATED)
async def create_plan(
    payload: UpdatePlanCreate, session: AsyncSession = Depends(get_session)
) -> UpdatePlan:
    try:
        orchestration.validate_steps([step.model_dump() for step in payload.steps])
    except orchestration.PlanValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    plan = await repository.create_update_plan(session, payload)
    await session.commit()
    return plan


@app.get("/plans", response_model=list[UpdatePlanOut])
async def list_plans(session: AsyncSession = Depends(get_session)) -> list[UpdatePlan]:
    return await repository.list_update_plans(session)


@app.get("/plans/{plan_id}", response_model=UpdatePlanOut)
async def get_plan(plan_id: str, session: AsyncSession = Depends(get_session)) -> UpdatePlan:
    try:
        return await repository.get_update_plan(session, plan_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _run_out(
    session: AsyncSession, run: InstallationRun, plan: UpdatePlan
) -> InstallationRunOut:
    installation = await repository.get_managed_installation(session, run.installation_id)
    step = repository.current_step(plan, run)
    return InstallationRunOut(
        id=run.id,
        installation_id=run.installation_id,
        installation_display_name=installation.display_name,
        current_step_index=run.current_step_index,
        current_step_name=step["name"] if step else None,
        status=run.status,
        last_outcome=run.last_outcome,
        error_message=run.error_message,
        proposed_by=run.proposed_by,
        started_at=run.started_at,
        updated_at=run.updated_at,
        completed_at=run.completed_at,
    )


async def _rollout_out(session: AsyncSession, rollout) -> RolloutOut:
    plan = await repository.get_update_plan(session, rollout.plan_id)
    runs = await repository.list_runs_for_rollout(session, rollout.id)
    return RolloutOut(
        id=rollout.id,
        plan_id=rollout.plan_id,
        name=rollout.name,
        group_id=rollout.group_id,
        status=rollout.status,
        created_at=rollout.created_at,
        started_at=rollout.started_at,
        started_by=rollout.started_by,
        runs=[await _run_out(session, r, plan) for r in runs],
    )


@app.post("/rollouts", response_model=RolloutOut, status_code=status.HTTP_201_CREATED)
async def create_rollout(payload: RolloutCreate, session: AsyncSession = Depends(get_session)):
    try:
        rollout, _runs = await repository.create_rollout(session, payload)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return await _rollout_out(session, rollout)


@app.get("/rollouts", response_model=list[RolloutOut])
async def list_rollouts(session: AsyncSession = Depends(get_session)):
    rollouts = await repository.list_rollouts(session)
    return [await _rollout_out(session, r) for r in rollouts]


@app.get("/rollouts/{rollout_id}", response_model=RolloutOut)
async def get_rollout(rollout_id: str, session: AsyncSession = Depends(get_session)):
    try:
        rollout = await repository.get_rollout(session, rollout_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await _rollout_out(session, rollout)


@app.post("/rollouts/{rollout_id}/start", response_model=RolloutOut)
async def start_rollout(
    rollout_id: str, payload: RolloutStart, session: AsyncSession = Depends(get_session)
):
    """3a: "eine Welle muss explizit gestartet werden" - setzt jeden noch
    nicht gestarteten `InstallationRun` auf den Wartezustand seines ersten
    Schritts (``wait_external``); ein `verify`-Erstschritt muss trotzdem
    explizit über `POST .../advance` versucht werden, kein impliziter
    Auto-Start (ein Schritttyp-Verhalten, keine Sonderregel für Schritt 0)."""
    try:
        rollout = await repository.get_rollout(session, rollout_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if rollout.status != "draft":
        raise HTTPException(
            status_code=409, detail=f"Rollout ist bereits im Status {rollout.status!r}"
        )
    await repository.start_rollout(session, rollout, started_by=payload.started_by)
    runs = await repository.list_runs_for_rollout(session, rollout_id)
    for run in runs:
        await repository.set_run_status(session, run, status="wait_external")
    await session.commit()
    return await _rollout_out(session, rollout)


async def _load_run_context(session: AsyncSession, rollout_id: str, installation_id: str):
    try:
        rollout = await repository.get_rollout(session, rollout_id)
        run = await repository.get_run(session, rollout_id, installation_id)
        plan = await repository.get_update_plan(session, rollout.plan_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return rollout, run, plan


@app.post(
    "/rollouts/{rollout_id}/installations/{installation_id}/advance",
    response_model=InstallationRunOut,
)
async def advance_installation_run(
    rollout_id: str, installation_id: str, session: AsyncSession = Depends(get_session)
):
    """Nur für ``step_type="verify"``-Schritte - ruft `_fetch_status()` der
    Zielinstallation ab und wertet das Ergebnis in eine der fünf
    Fehlerentscheidungen (oder Erfolg) um. ``"gate"``-Schritte werden nicht
    hierüber, sondern über `mark-done` bestätigt (siehe dort)."""
    rollout, run, plan = await _load_run_context(session, rollout_id, installation_id)
    step = repository.current_step(plan, run)
    if step is None:
        raise HTTPException(
            status_code=400, detail="Rollout für diese Installation bereits abgeschlossen"
        )
    if step["step_type"] != "verify":
        raise HTTPException(
            status_code=400,
            detail="Aktueller Schritt ist ein 'gate'-Schritt - mit mark-done bestätigen",
        )
    if run.status not in {"wait_external", "retry_later"}:
        raise HTTPException(
            status_code=409,
            detail=f"Schritt ist im Status {run.status!r} - advance hier nicht anwendbar",
        )
    installation = await repository.get_managed_installation(session, installation_id)
    status_out = await _fetch_status(installation)
    if not status_out.reachable:
        await repository.set_run_status(
            session,
            run,
            status="retry_later",
            last_outcome="retry_later",
            error_message=status_out.error,
        )
    elif status_out.license_status is None or status_out.license_status.get("valid") is False:
        await repository.set_run_status(
            session,
            run,
            status="recoverable_failed",
            last_outcome="recoverable_failed",
            error_message="Installation erreichbar, aber Lizenzstatus nach dem Schritt ungültig",
        )
    else:
        await repository.advance_run(session, run, plan)
    await session.commit()
    return await _run_out(session, run, plan)


@app.post(
    "/rollouts/{rollout_id}/installations/{installation_id}/mark-done",
    response_model=InstallationRunOut,
)
async def mark_installation_run_done(
    rollout_id: str,
    installation_id: str,
    payload: MarkDoneRequest,
    session: AsyncSession = Depends(get_session),
):
    """Bestätigt einen ``"gate"``-Schritt (Bereichssperre/Wartungsmodus
    gesetzt, Rolling Update durchgeführt, Backup gezogen, final freigegeben -
    3a) - die tatsächliche Ausführung liegt außerhalb dieses Service (siehe
    ADR 0038), `payload.outcome` lässt den Bediener, der die Aktion
    durchgeführt hat, jede der vier meldbaren Fehlerentscheidungen wählen."""
    rollout, run, plan = await _load_run_context(session, rollout_id, installation_id)
    step = repository.current_step(plan, run)
    if step is None or step["step_type"] != "gate":
        raise HTTPException(
            status_code=400,
            detail="Aktueller Schritt ist kein 'gate'-Schritt oder bereits abgeschlossen",
        )
    if run.status != "wait_external":
        raise HTTPException(
            status_code=409,
            detail=f"Schritt ist im Status {run.status!r} - mark-done hier nicht anwendbar",
        )
    if payload.outcome not in orchestration.REPORTABLE_GATE_OUTCOMES:
        raise HTTPException(status_code=422, detail=f"Unbekanntes outcome {payload.outcome!r}")
    if payload.outcome != "success":
        await repository.set_run_status(
            session,
            run,
            status=payload.outcome,
            last_outcome=payload.outcome,
            error_message=payload.detail,
        )
    elif step.get("requires_approval"):
        await repository.propose_run(session, run, actor=payload.actor)
    else:
        await repository.advance_run(session, run, plan)
    await session.commit()
    return await _run_out(session, run, plan)


@app.post(
    "/rollouts/{rollout_id}/installations/{installation_id}/approve",
    response_model=InstallationRunOut,
)
async def approve_installation_run(
    rollout_id: str,
    installation_id: str,
    payload: ApprovalDecision,
    session: AsyncSession = Depends(get_session),
):
    """Wiederverwendung des generischen Vier-Augen-Grundprinzips (4.3) auf
    Fleet-Ebene: getrennter Vorschlags-/Freigabeschritt, erzwungen über zwei
    getrennte API-Aufrufe (`mark-done` schlägt vor, `approve` bestätigt) -
    ``actor`` muss vom ursprünglichen `proposed_by` abweichen. Anders als
    `permission-service`s echtes 4.3 führt fleet-management-service keine
    eigene Nutzerverwaltung (siehe ADR 0038) - die Trennung ist strukturell
    (zwei Aufrufe), nicht kryptografisch zwischen zwei echten Identitäten
    erzwungen."""
    rollout, run, plan = await _load_run_context(session, rollout_id, installation_id)
    if run.status != "manual_required":
        raise HTTPException(
            status_code=409, detail=f"Schritt ist im Status {run.status!r} - keine offene Freigabe"
        )
    if payload.actor == run.proposed_by:
        raise HTTPException(
            status_code=409,
            detail="Freigabe durch dieselbe Person wie der Vorschlag ist nicht erlaubt (4.3)",
        )
    await repository.advance_run(session, run, plan)
    await session.commit()
    return await _run_out(session, run, plan)


@app.post(
    "/rollouts/{rollout_id}/installations/{installation_id}/reject",
    response_model=InstallationRunOut,
)
async def reject_installation_run(
    rollout_id: str,
    installation_id: str,
    payload: ApprovalDecision,
    session: AsyncSession = Depends(get_session),
):
    rollout, run, plan = await _load_run_context(session, rollout_id, installation_id)
    if run.status != "manual_required":
        raise HTTPException(
            status_code=409, detail=f"Schritt ist im Status {run.status!r} - keine offene Freigabe"
        )
    await repository.set_run_status(
        session,
        run,
        status="recoverable_failed",
        last_outcome="recoverable_failed",
        error_message=f"Abgelehnt von {payload.actor}: {payload.reason or 'kein Grund angegeben'}",
    )
    await session.commit()
    return await _run_out(session, run, plan)


@app.post(
    "/rollouts/{rollout_id}/installations/{installation_id}/retry",
    response_model=InstallationRunOut,
)
async def retry_installation_run(
    rollout_id: str, installation_id: str, session: AsyncSession = Depends(get_session)
):
    """Für ``retry_later``/``recoverable_failed`` (3a: "die Bedienperson kann
    gezielt erneut versuchen") - setzt den aktuellen Schritt zurück in den
    Wartezustand, ohne den Schrittindex zu verändern."""
    rollout, run, plan = await _load_run_context(session, rollout_id, installation_id)
    if run.status not in {"retry_later", "recoverable_failed"}:
        raise HTTPException(
            status_code=409,
            detail=f"Schritt ist im Status {run.status!r} - retry hier nicht anwendbar",
        )
    await repository.set_run_status(session, run, status="wait_external")
    await session.commit()
    return await _run_out(session, run, plan)


@app.post(
    "/rollouts/{rollout_id}/installations/{installation_id}/acknowledge-fatal",
    response_model=InstallationRunOut,
)
async def acknowledge_fatal_installation_run(
    rollout_id: str, installation_id: str, session: AsyncSession = Depends(get_session)
):
    """Für ``fatal_contract`` (3a: "muss vor jedem weiteren Versuch korrigiert
    werden") - bewusst ein eigener, expliziter Endpunkt statt `retry`: eine
    Bedienperson muss aktiv bestätigen, dass Plan/Konfiguration korrigiert
    wurden, bevor ein neuer Versuch überhaupt erst möglich wird."""
    rollout, run, plan = await _load_run_context(session, rollout_id, installation_id)
    if run.status != "fatal_contract":
        raise HTTPException(
            status_code=409,
            detail=f"Schritt ist im Status {run.status!r} - acknowledge-fatal hier nicht anwendbar",
        )
    await repository.set_run_status(session, run, status="wait_external")
    await session.commit()
    return await _run_out(session, run, plan)
