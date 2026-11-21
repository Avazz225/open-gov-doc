"""ocr-service sensors (P88-S2b, package B). OCR has no queue - a result row
is written after processing - so the backlog worth watching is the failed
results: `failed` ones are retried with backoff, `failed_permanent` ones
gave up and need someone to look."""

from __future__ import annotations

from dms_metrics_client import SensorRegistry, SensorSpec
from ocr_service.models import OcrResult
from sqlalchemy import func, select

FAILURES = SensorSpec(
    name="ocr.results.failed",
    group="reliability",
    cost="cheap",
    description="Fehlgeschlagene OCR-Ergebnisse je Zustand (retrying = wird wiederholt, permanent)",
)
NEEDS_REVIEW = SensorSpec(
    name="ocr.results.needs_review",
    group="reliability",
    cost="cheap",
    description="OCR-Ergebnisse mit geringer Erkennungsqualitaet, die auf Pruefung warten",
)

_STATE = {"failed": "retrying", "failed_permanent": "permanent"}


def build_sensors(registry: SensorRegistry, app) -> None:
    async def failures() -> dict[tuple[str, ...], float]:
        async with app.state.session_factory() as session:
            rows = await session.execute(
                select(OcrResult.status, func.count())
                .where(OcrResult.status.in_(_STATE))
                .group_by(OcrResult.status)
            )
        counts = dict(rows.all())
        return {(state,): float(counts.get(status, 0)) for status, state in _STATE.items()}

    async def needs_review() -> float:
        async with app.state.session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(OcrResult)
                .where(OcrResult.status == "needs_review")
            )
        return float(count or 0)

    registry.on_scrape_async(registry.gauge(FAILURES, labelnames=("state",)), failures)
    registry.on_scrape_async(registry.gauge(NEEDS_REVIEW), needs_review)
