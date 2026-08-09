import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fleet_management_service.models import (
    InstallationGroup,
    InstallationGroupMember,
    InstallationRun,
    ManagedInstallation,
    Rollout,
    UpdatePlan,
)
from fleet_management_service.schemas import (
    ManagedInstallationCreate,
    RolloutCreate,
    UpdatePlanCreate,
)


class NotFoundError(Exception):
    pass


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


async def create_managed_installation(
    session: AsyncSession, payload: ManagedInstallationCreate
) -> tuple[ManagedInstallation, str]:
    now = datetime.now(UTC)
    api_key = payload.fleet_agent_api_key or generate_api_key()
    installation = ManagedInstallation(
        id=str(uuid.uuid4()),
        display_name=payload.display_name,
        gateway_base_url=payload.gateway_base_url,
        fleet_agent_api_key=api_key,
        created_at=now,
        updated_at=now,
    )
    session.add(installation)
    await session.flush()
    return installation, api_key


async def list_managed_installations(session: AsyncSession) -> list[ManagedInstallation]:
    result = await session.execute(
        select(ManagedInstallation).order_by(ManagedInstallation.display_name)
    )
    return list(result.scalars().all())


async def get_managed_installation(
    session: AsyncSession, installation_id: str
) -> ManagedInstallation:
    installation = await session.get(ManagedInstallation, installation_id)
    if installation is None:
        raise NotFoundError(f"installation_id {installation_id!r} unbekannt")
    return installation


async def delete_managed_installation(session: AsyncSession, installation_id: str) -> None:
    installation = await get_managed_installation(session, installation_id)
    await session.delete(installation)
    await session.flush()


# --- Flotten-Update-Orchestrierung (3a-Erweiterung, P13-S2b) ----------------


async def create_group(session: AsyncSession, name: str) -> InstallationGroup:
    group = InstallationGroup(id=str(uuid.uuid4()), name=name, created_at=datetime.now(UTC))
    session.add(group)
    await session.flush()
    return group


async def get_group(session: AsyncSession, group_id: str) -> InstallationGroup:
    group = await session.get(InstallationGroup, group_id)
    if group is None:
        raise NotFoundError(f"group_id {group_id!r} unbekannt")
    return group


async def list_groups(session: AsyncSession) -> list[InstallationGroup]:
    result = await session.execute(select(InstallationGroup).order_by(InstallationGroup.name))
    return list(result.scalars().all())


async def delete_group(session: AsyncSession, group_id: str) -> None:
    group = await get_group(session, group_id)
    await session.delete(group)
    await session.flush()


async def list_group_member_ids(session: AsyncSession, group_id: str) -> list[str]:
    result = await session.execute(
        select(InstallationGroupMember.installation_id).where(
            InstallationGroupMember.group_id == group_id
        )
    )
    return list(result.scalars().all())


async def add_group_member(session: AsyncSession, group_id: str, installation_id: str) -> None:
    await get_group(session, group_id)
    await get_managed_installation(session, installation_id)
    existing = await session.get(InstallationGroupMember, (group_id, installation_id))
    if existing is not None:
        return
    session.add(InstallationGroupMember(group_id=group_id, installation_id=installation_id))
    await session.flush()


async def remove_group_member(session: AsyncSession, group_id: str, installation_id: str) -> None:
    member = await session.get(InstallationGroupMember, (group_id, installation_id))
    if member is None:
        raise NotFoundError(f"{installation_id!r} ist kein Mitglied von Gruppe {group_id!r}")
    await session.delete(member)
    await session.flush()


async def create_update_plan(session: AsyncSession, payload: UpdatePlanCreate) -> UpdatePlan:
    plan = UpdatePlan(
        id=str(uuid.uuid4()),
        name=payload.name,
        version=payload.version,
        steps=[step.model_dump() for step in payload.steps],
        created_at=datetime.now(UTC),
    )
    session.add(plan)
    await session.flush()
    return plan


async def list_update_plans(session: AsyncSession) -> list[UpdatePlan]:
    result = await session.execute(select(UpdatePlan).order_by(UpdatePlan.name, UpdatePlan.version))
    return list(result.scalars().all())


async def get_update_plan(session: AsyncSession, plan_id: str) -> UpdatePlan:
    plan = await session.get(UpdatePlan, plan_id)
    if plan is None:
        raise NotFoundError(f"plan_id {plan_id!r} unbekannt")
    return plan


async def resolve_rollout_installation_ids(
    session: AsyncSession, *, group_id: str | None, include: list[str], exclude: list[str]
) -> list[str]:
    """3a: "Installationen lassen sich gezielt ein-/ausschließen, ohne den
    Plan für die übrigen Installationen zu verändern" - Gruppenmitglieder
    plus `include`, minus `exclude`."""
    base_ids: set[str] = set()
    if group_id is not None:
        await get_group(session, group_id)
        base_ids.update(await list_group_member_ids(session, group_id))
    base_ids.update(include)
    base_ids.difference_update(exclude)
    for installation_id in base_ids:
        await get_managed_installation(session, installation_id)
    if not base_ids:
        raise NotFoundError("Kein Installations-Zielkreis - Gruppe leer und kein include gesetzt")
    return sorted(base_ids)


async def create_rollout(
    session: AsyncSession, payload: RolloutCreate
) -> tuple[Rollout, list[InstallationRun]]:
    await get_update_plan(session, payload.plan_id)
    installation_ids = await resolve_rollout_installation_ids(
        session, group_id=payload.group_id, include=payload.include, exclude=payload.exclude
    )
    now = datetime.now(UTC)
    rollout = Rollout(
        id=str(uuid.uuid4()),
        plan_id=payload.plan_id,
        name=payload.name,
        group_id=payload.group_id,
        include=payload.include,
        exclude=payload.exclude,
        status="draft",
        created_at=now,
    )
    session.add(rollout)
    await session.flush()
    runs = []
    for installation_id in installation_ids:
        run = InstallationRun(
            id=str(uuid.uuid4()),
            rollout_id=rollout.id,
            installation_id=installation_id,
            current_step_index=0,
            status="pending",
            updated_at=now,
        )
        session.add(run)
        runs.append(run)
    await session.flush()
    return rollout, runs


async def get_rollout(session: AsyncSession, rollout_id: str) -> Rollout:
    rollout = await session.get(Rollout, rollout_id)
    if rollout is None:
        raise NotFoundError(f"rollout_id {rollout_id!r} unbekannt")
    return rollout


async def list_rollouts(session: AsyncSession) -> list[Rollout]:
    result = await session.execute(select(Rollout).order_by(Rollout.created_at.desc()))
    return list(result.scalars().all())


async def list_runs_for_rollout(session: AsyncSession, rollout_id: str) -> list[InstallationRun]:
    result = await session.execute(
        select(InstallationRun)
        .where(InstallationRun.rollout_id == rollout_id)
        .order_by(InstallationRun.id)
    )
    return list(result.scalars().all())


async def get_run(session: AsyncSession, rollout_id: str, installation_id: str) -> InstallationRun:
    result = await session.execute(
        select(InstallationRun).where(
            InstallationRun.rollout_id == rollout_id,
            InstallationRun.installation_id == installation_id,
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise NotFoundError(
            f"Kein InstallationRun fuer installation_id {installation_id!r} in Rollout "
            f"{rollout_id!r}"
        )
    return run


async def start_rollout(session: AsyncSession, rollout: Rollout, *, started_by: str) -> None:
    rollout.status = "running"
    rollout.started_at = datetime.now(UTC)
    rollout.started_by = started_by
    await session.flush()


def current_step(plan: UpdatePlan, run: InstallationRun) -> dict | None:
    if run.current_step_index >= len(plan.steps):
        return None
    return plan.steps[run.current_step_index]


async def set_run_status(
    session: AsyncSession,
    run: InstallationRun,
    *,
    status: str,
    last_outcome: str | None = None,
    error_message: str | None = None,
) -> None:
    run.status = status
    run.last_outcome = last_outcome
    run.error_message = error_message
    run.updated_at = datetime.now(UTC)
    if run.started_at is None and status != "pending":
        run.started_at = run.updated_at
    await session.flush()


async def advance_run(session: AsyncSession, run: InstallationRun, plan: UpdatePlan) -> str:
    """Rückt einen erfolgreich abgeschlossenen Schritt weiter. Gibt den neuen
    Status zurück (``"wait_external"`` fuer den naechsten Gate-Schritt,
    ``"completed"`` wenn das der letzte Schritt war) - der eigentliche
    ``"verify"``-Sofortversuch fuer einen neuen Schritt passiert in
    `main.py`, nicht hier (reines I/O-freies Zustandsupdate)."""
    run.current_step_index += 1
    run.last_outcome = None
    run.error_message = None
    run.proposed_by = None
    run.updated_at = datetime.now(UTC)
    if current_step(plan, run) is None:
        run.status = "completed"
        run.completed_at = run.updated_at
    else:
        run.status = "wait_external"
    await session.flush()
    return run.status


async def propose_run(session: AsyncSession, run: InstallationRun, *, actor: str) -> None:
    run.status = "manual_required"
    run.proposed_by = actor
    run.updated_at = datetime.now(UTC)
    await session.flush()
