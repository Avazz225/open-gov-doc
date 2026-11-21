"""License sensors (P88-S2b, package A): days until the license expires and
the current usage of the two counted dimensions.

The values come from the poll loop's usage snapshot (`poll_loop.run_tick`),
which already asks auth-service and document-service every tick - the
sensors read that snapshot on scrape instead of repeating the HTTP calls."""

from __future__ import annotations

from dms_metrics_client import SensorRegistry, SensorSpec

from license_service.usage import UsageSnapshot

EXPIRATION_DAYS = SensorSpec(
    name="license.expiration.days",
    group="capacity",
    cost="cheap",
    description="Tage bis zum Ablauf der installierten Lizenz",
)
USERS_CURRENT = SensorSpec(
    name="license.usage.users.current",
    group="capacity",
    cost="cheap",
    description="Aktuell gezaehlte Nutzer (named oder concurrent, je nach Lizenzmodell)",
)
DOCUMENTS_CURRENT = SensorSpec(
    name="license.usage.documents.current",
    group="capacity",
    cost="cheap",
    description="Aktuell gezaehlte aktive Dokumente gegen das Lizenzlimit",
)

_latest: UsageSnapshot | None = None


def record_snapshot(snapshot: UsageSnapshot | None) -> None:
    global _latest
    _latest = snapshot


def _read(pick):
    def sample() -> float | None:
        if _latest is None:
            return None
        value = pick(_latest)
        return None if value is None else float(value)

    return sample


def build_sensors(registry: SensorRegistry) -> None:
    # Optional gauges: without an installed license there is no expiry and
    # no counted usage - absent, not a misleading 0.
    registry.on_scrape(
        registry.gauge(EXPIRATION_DAYS, optional=True), _read(lambda s: s.days_remaining)
    )
    registry.on_scrape(
        registry.gauge(USERS_CURRENT, optional=True), _read(lambda s: s.users.current)
    )
    registry.on_scrape(
        registry.gauge(DOCUMENTS_CURRENT, optional=True), _read(lambda s: s.documents.current)
    )
