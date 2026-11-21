"""auth-service sensors (P88-S2b): active sessions (package A) and logins
by method and result (package D, security)."""

from __future__ import annotations

from dms_metrics_client import GuardedCounter, SensorRegistry, SensorSpec

SESSIONS_ACTIVE = SensorSpec(
    name="auth.sessions.active",
    group="capacity",
    cost="cheap",
    description="Aktive Keycloak-Sitzungen des DMS-Clients",
)
LOGINS = SensorSpec(
    name="auth.logins",
    group="security",
    cost="cheap",
    description=(
        "Anmeldeversuche je Methode (technical_account, keycloak, sso, password) "
        "und Ergebnis (success, rejected, maintenance)"
    ),
)

_logins: GuardedCounter | None = None


def build_sensors(registry: SensorRegistry, count_sessions) -> None:
    """`count_sessions` is an async callable returning the active session
    count - it asks Keycloak, so it only runs while the sensor is on."""
    global _logins
    _logins = registry.counter(LOGINS, labelnames=("method", "result"))
    registry.on_scrape_async(registry.gauge(SESSIONS_ACTIVE), count_sessions)


def count_login(method: str, result: str) -> None:
    if _logins is not None:
        _logins.inc(method=method, result=result)
