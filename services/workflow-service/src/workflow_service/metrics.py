"""workflow-service sensors (P88-S2b, package B). Process instances have no
"failed" status and user tasks no due-date column (tasks live in the BPMN
state, ADR 0019), so the approved "failed instances / overdue tasks" map to
what exists: running instances, and SLA escalations - a task whose BPMN
deadline timer fired, which is the workflow's own definition of overdue."""

from __future__ import annotations

from dms_metrics_client import GuardedCounter, SensorRegistry, SensorSpec
from sqlalchemy import func, select

from workflow_service.models import ProcessInstance

RUNNING = SensorSpec(
    name="workflow.instances.running",
    group="capacity",
    cost="cheap",
    description="Laufende Prozessinstanzen",
)
ESCALATED = SensorSpec(
    name="workflow.tasks.escalated",
    group="reliability",
    cost="cheap",
    description=(
        "Aufgaben, deren SLA-Frist abgelaufen ist (Eskalation ausgeloest), seit Prozessstart"
    ),
)

_escalated: GuardedCounter | None = None


def build_sensors(registry: SensorRegistry, app) -> None:
    global _escalated
    _escalated = registry.counter(ESCALATED)

    async def running() -> float:
        async with app.state.session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(ProcessInstance)
                .where(ProcessInstance.status == "running")
            )
        return float(count or 0)

    registry.on_scrape_async(registry.gauge(RUNNING), running)


def count_escalation() -> None:
    if _escalated is not None:
        _escalated.inc()
