"""notification-service sensor (P88-S2b, package B): deliveries that failed,
per channel (email, webhook, in_app) and state - `failed` is retried with
backoff, `failed_permanent` gave up."""

from __future__ import annotations

from dms_metrics_client import SensorRegistry, SensorSpec
from sqlalchemy import func, select

from notification_service.models import Notification

FAILURES = SensorSpec(
    name="notification.deliveries.failed",
    group="reliability",
    cost="cheap",
    description="Fehlgeschlagene Zustellungen je Kanal (email, webhook, in_app) und Zustand",
)

_STATE = {"failed": "retrying", "failed_permanent": "permanent"}


def build_sensors(registry: SensorRegistry, app) -> None:
    async def failures() -> dict[tuple[str, ...], float]:
        async with app.state.session_factory() as session:
            rows = await session.execute(
                select(Notification.channel, Notification.status, func.count())
                .where(Notification.status.in_(_STATE))
                .group_by(Notification.channel, Notification.status)
            )
        return {(channel, _STATE[status]): float(n) for channel, status, n in rows.all()}

    registry.on_scrape_async(registry.gauge(FAILURES, labelnames=("channel", "state")), failures)
