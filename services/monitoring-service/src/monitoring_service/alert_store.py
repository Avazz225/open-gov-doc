"""Alert rules, states and downtime windows in the database, and one
evaluation tick (P88-S5, ADR 0245). The evaluation itself is `alerting.py`."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from monitoring_service import alerting
from monitoring_service.models import AlertRule, AlertState, DowntimeWindow
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

# Key of the Postgres advisory lock that makes one replica the evaluator
# per tick (Decision 4). Any constant; "MONALERT" in ASCII.
ALERT_LOCK_KEY = 0x4D4F4E414C455254


class NotFoundError(Exception):
    pass


def thresholds_of(rule: AlertRule) -> alerting.Thresholds:
    return alerting.Thresholds(rule.min_error, rule.min_warning, rule.max_warning, rule.max_error)


# --- Rules -------------------------------------------------------------------


async def list_rules(session: AsyncSession) -> list[AlertRule]:
    result = await session.execute(select(AlertRule).order_by(AlertRule.sensor, AlertRule.name))
    return list(result.scalars().all())


async def get_rule(session: AsyncSession, rule_id: str) -> AlertRule:
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        raise NotFoundError(f"Alarmregel {rule_id!r} unbekannt")
    return rule


async def save_rule(
    session: AsyncSession, values: dict, *, actor: str, now: datetime, rule_id: str | None = None
) -> AlertRule:
    alerting.validate_rule(
        alerting.Thresholds(
            values.get("min_error"),
            values.get("min_warning"),
            values.get("max_warning"),
            values.get("max_error"),
        ),
        values["aggregation"],
        values["warning_repeat"],
        values["error_repeat"],
    )
    if rule_id is None:
        rule = AlertRule(id=str(uuid.uuid4()))
        session.add(rule)
    else:
        rule = await get_rule(session, rule_id)
        # A changed definition starts its streaks and rate afresh; the
        # notified level stays, so a still-active alarm isn't mailed twice.
        state = await session.get(AlertState, rule_id)
        if state is not None:
            state.warning_streak = state.error_streak = 0
            state.last_total = state.last_total_at = None
    for key, value in values.items():
        setattr(rule, key, value)
    rule.updated_at = now
    rule.updated_by = actor
    await session.flush()
    return rule


async def delete_rule(session: AsyncSession, rule_id: str) -> None:
    rule = await get_rule(session, rule_id)
    await session.execute(delete(AlertState).where(AlertState.rule_id == rule_id))
    await session.delete(rule)
    await session.flush()


async def states_by_rule(session: AsyncSession) -> dict[str, AlertState]:
    result = await session.execute(select(AlertState))
    return {state.rule_id: state for state in result.scalars().all()}


# --- Downtime windows ------------------------------------------------------------


async def list_windows(session: AsyncSession) -> list[DowntimeWindow]:
    result = await session.execute(select(DowntimeWindow).order_by(DowntimeWindow.name))
    return list(result.scalars().all())


async def save_window(
    session: AsyncSession, values: dict, *, actor: str, now: datetime, window_id: str | None = None
) -> DowntimeWindow:
    alerting.validate_time(values.get("daily_start"))
    alerting.validate_time(values.get("daily_end"))
    if bool(values.get("daily_start")) != bool(values.get("daily_end")):
        raise ValueError("Tägliches Fenster braucht Beginn und Ende")
    starts, ends = values.get("starts_at"), values.get("ends_at")
    if starts and ends and ends <= starts:
        raise ValueError("Ende muss nach dem Beginn liegen")
    if not any([starts, ends, values.get("weekdays"), values.get("daily_start")]):
        raise ValueError("Ein Wartungsfenster braucht einen Zeitraum, Wochentage oder Uhrzeiten")
    if window_id is None:
        window = DowntimeWindow(id=str(uuid.uuid4()))
        session.add(window)
    else:
        window = await session.get(DowntimeWindow, window_id)
        if window is None:
            raise NotFoundError(f"Wartungsfenster {window_id!r} unbekannt")
    for key, value in values.items():
        setattr(window, key, value)
    window.updated_at = now
    window.updated_by = actor
    await session.flush()
    return window


async def delete_window(session: AsyncSession, window_id: str) -> None:
    window = await session.get(DowntimeWindow, window_id)
    if window is None:
        raise NotFoundError(f"Wartungsfenster {window_id!r} unbekannt")
    await session.delete(window)
    await session.flush()


def window_covers(window: DowntimeWindow, rule_id: str, now: datetime, timezone: str) -> bool:
    if window.rule_ids and rule_id not in window.rule_ids:
        return False
    return alerting.window_active(
        enabled=window.enabled,
        starts_at=window.starts_at,
        ends_at=window.ends_at,
        weekdays=window.weekdays,
        daily_start=window.daily_start,
        daily_end=window.daily_end,
        now=now,
        timezone=timezone,
    )


# --- One tick ---------------------------------------------------------------------


@dataclass
class TickResult:
    events: list[tuple[str, str, dict]] = field(default_factory=list)
    evaluated: int = 0


async def tick(
    session: AsyncSession, families: dict, *, now: datetime, timezone: str
) -> TickResult:
    """Evaluates every enabled rule against the merged metric families.
    Returns the events to publish after commit: `state_changed` for every
    level change, `raised`/`resolved` when a notification is due and no
    downtime window covers the rule (Decision 3/5)."""
    result = TickResult()
    rules = [rule for rule in await list_rules(session) if rule.enabled]
    windows = await list_windows(session)
    states = await states_by_rule(session)
    for rule in rules:
        state = states.get(rule.id)
        if state is None:
            state = AlertState(
                rule_id=rule.id,
                level="unknown",
                since=now,
                evaluated_at=now,
                warning_streak=0,
                error_streak=0,
            )
            session.add(state)
        values = alerting.sample_values(
            families, rule.sensor, rule.aggregation, rule.label_filter or {}
        )
        measured = alerting.measure(
            values,
            rule.aggregation,
            last_total=state.last_total,
            last_at=state.last_total_at,
            now=now,
        )
        if measured.total is not None:
            state.last_total, state.last_total_at = measured.total, now
        level = alerting.next_level(
            measured.value,
            thresholds_of(rule),
            warning_repeat=rule.warning_repeat,
            error_repeat=rule.error_repeat,
            warning_streak=state.warning_streak,
            error_streak=state.error_streak,
        )
        previous = state.level
        state.value = measured.value
        state.warning_streak, state.error_streak = level.warning_streak, level.error_streak
        state.evaluated_at = now
        base = {
            "rule_id": rule.id,
            "name": rule.name,
            "sensor": rule.sensor,
            "value": measured.value,
            "label_filter": rule.label_filter or {},
        }
        if level.level != previous:
            state.level, state.since = level.level, now
            result.events.append(
                (
                    "monitoring.alert.state_changed",
                    rule.id,
                    {**base, "level": level.level, "previous_level": previous},
                )
            )
        due = alerting.notification_due(state.level, state.notified_level)
        state.suppressed = due is not None and any(
            window_covers(window, rule.id, now, timezone) for window in windows
        )
        if due and not state.suppressed:
            result.events.append(
                (
                    due,
                    rule.id,
                    {
                        **base,
                        "level": state.level,
                        "previous_level": state.notified_level,
                        "since": state.since.isoformat(),
                        "thresholds": {
                            "min_error": rule.min_error,
                            "min_warning": rule.min_warning,
                            "max_warning": rule.max_warning,
                            "max_error": rule.max_error,
                        },
                    },
                )
            )
            state.notified_level, state.notified_at = state.level, now
        result.evaluated += 1
    await session.flush()
    return result


# --- Prometheus export (Decision 1) -----------------------------------------------


def _selector(rule: AlertRule, family_types: dict[str, str]) -> str:
    family = alerting.family_name(rule.sensor)
    matchers = ",".join(
        f"{key}={json.dumps(value)}" for key, value in (rule.label_filter or {}).items()
    )
    braces = f"{{{matchers}}}" if matchers else ""
    if rule.aggregation == alerting.RATE_AGGREGATION:
        suffix = "_count" if family_types.get(family) == "histogram" else "_total"
        if family_types.get(family) == "gauge":
            suffix = ""
        return f"sum(rate({family}{suffix}{braces}[5m])) * 60"
    return f"{rule.aggregation}({family}{braces})"


def prometheus_rules(
    rules: list[AlertRule], family_types: dict[str, str], interval_seconds: float
) -> str:
    """The rules as a Prometheus rule file (YAML; strings are JSON-quoted,
    which YAML accepts). The repeat count becomes `for:`, interval × repeat."""
    lines = ["groups:", "  - name: dms-sensor-alerts", "    rules:"]
    for rule in rules:
        if not rule.enabled:
            continue
        expr = _selector(rule, family_types)
        for severity, low, high, repeat in (
            ("warning", rule.min_warning, rule.max_warning, rule.warning_repeat),
            ("critical", rule.min_error, rule.max_error, rule.error_repeat),
        ):
            conditions = []
            if low is not None:
                conditions.append(f"({expr}) < {low}")
            if high is not None:
                conditions.append(f"({expr}) > {high}")
            if not conditions:
                continue
            duration = int(max(repeat - 1, 0) * interval_seconds)
            lines += [
                f"      - alert: {json.dumps(f'{rule.name} ({severity})')}",
                f"        expr: {json.dumps(' or '.join(conditions))}",
                f"        for: {duration}s",
                "        labels:",
                f"          severity: {severity}",
                f"          dms_rule_id: {json.dumps(rule.id)}",
                "        annotations:",
                f"          summary: {json.dumps(f'{rule.sensor}: {rule.name}')}",
            ]
    return "\n".join(lines) + "\n"
