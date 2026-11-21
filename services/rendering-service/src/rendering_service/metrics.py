"""rendering-service sensors (P88-S2b, package B): failed renditions per
kind and state, and a counter of renditions produced per kind. Like OCR
there is no queue; rows are written after processing."""

from __future__ import annotations

from dms_metrics_client import GuardedCounter, SensorRegistry, SensorSpec
from sqlalchemy import func, select

from rendering_service.models import Rendition

FAILURES = SensorSpec(
    name="rendering.renditions.failed",
    group="reliability",
    cost="cheap",
    description="Fehlgeschlagene Renditions je Art (thumbnail, pdf_archive, ...) und Zustand",
)
PRODUCED = SensorSpec(
    name="rendering.renditions.produced",
    group="performance",
    cost="cheap",
    description="Erfolgreich erzeugte Renditions je Art seit Prozessstart",
)

_STATE = {"failed": "retrying", "failed_permanent": "permanent"}
_produced: GuardedCounter | None = None


def build_sensors(registry: SensorRegistry, app) -> None:
    global _produced
    _produced = registry.counter(PRODUCED, labelnames=("kind",))

    async def failures() -> dict[tuple[str, ...], float]:
        async with app.state.session_factory() as session:
            rows = await session.execute(
                select(Rendition.rendition_type, Rendition.status, func.count())
                .where(Rendition.status.in_(_STATE))
                .group_by(Rendition.rendition_type, Rendition.status)
            )
        return {(kind, _STATE[status]): float(n) for kind, status, n in rows.all()}

    registry.on_scrape_async(registry.gauge(FAILURES, labelnames=("kind", "state")), failures)


def count_produced(kind: str) -> None:
    if _produced is not None:
        _produced.inc(kind=kind)
