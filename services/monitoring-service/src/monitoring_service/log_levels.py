"""Central log levels (P97-S1, ADR 0267): storing, validating, expiring and
resolving them.

Scopes, from broad to specific:
- `global` - the root level of every service;
- `service:<service>` - the root level of one service (all its replicas);
- `logger:<service|*>:<logger>` - one logger (and its children) in one service
  or in all.

The distributed form (`distributed()`) goes out with `GET /sensor-config`;
`dms_common.logging.apply_log_levels` applies it in every replica. Expired
entries are left out right away and removed by `purge_expired`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from dms_common.logging import LEVELS, resolve_log_levels
from monitoring_service.models import LogLevelSetting
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

ALL_SERVICES = "*"
_SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")
_LOGGER_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,199}$")


class LogLevelError(ValueError):
    """Invalid input; the message is shown to the admin."""


@dataclass(frozen=True)
class Scope:
    service: str | None  # None = global; "*" only together with a logger
    logger: str | None

    @property
    def key(self) -> str:
        if self.logger:
            return f"logger:{self.service or ALL_SERVICES}:{self.logger}"
        if self.service:
            return f"service:{self.service}"
        return "global"

    @classmethod
    def parse(cls, key: str) -> Scope:
        if key == "global":
            return cls(None, None)
        kind, _, rest = key.partition(":")
        if kind == "service":
            return cls(rest, None)
        if kind == "logger":
            service, _, logger = rest.partition(":")
            return cls(service, logger)
        raise LogLevelError(f"Unbekannter Bereich {key!r}")


def make_scope(service: str | None, logger: str | None) -> Scope:
    service = (service or "").strip() or None
    logger = (logger or "").strip() or None
    if service == ALL_SERVICES and not logger:
        raise LogLevelError(
            "'*' gilt nur zusammen mit einem Logger; für alle Dienste den globalen Level setzen"
        )
    if service and service != ALL_SERVICES and not _SERVICE_RE.match(service):
        raise LogLevelError(f"Ungültiger Dienstname {service!r}")
    if logger and not _LOGGER_RE.match(logger):
        raise LogLevelError(f"Ungültiger Logger-Name {logger!r}")
    return Scope(service if service or not logger else ALL_SERVICES, logger)


def normalize_level(level: str) -> str:
    value = (level or "").strip().upper()
    if value == "WARN":
        value = "WARNING"
    if value not in LEVELS:
        raise LogLevelError(f"Unbekannter Level {level!r} - erlaubt: {', '.join(LEVELS)}")
    return value


def resolve_expiry(
    level: str,
    *,
    now: datetime,
    expires_in_minutes: int | None,
    expires_at: datetime | None,
    debug_default_minutes: int,
    debug_max_minutes: int,
) -> datetime | None:
    """DEBUG always expires (default duration, capped); other levels only
    when asked to."""
    if expires_in_minutes is not None and expires_at is not None:
        raise LogLevelError("Nur eines von Dauer oder Ablaufzeitpunkt angeben")
    if expires_in_minutes is not None:
        if expires_in_minutes < 1:
            raise LogLevelError("Die Dauer muss mindestens 1 Minute sein")
        expiry = now + timedelta(minutes=expires_in_minutes)
    elif expires_at is not None:
        expiry = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
        if expiry <= now:
            raise LogLevelError("Der Ablaufzeitpunkt liegt in der Vergangenheit")
    elif level == "DEBUG":
        expiry = now + timedelta(minutes=debug_default_minutes)
    else:
        return None
    if level == "DEBUG" and expiry > now + timedelta(minutes=debug_max_minutes):
        raise LogLevelError(
            f"DEBUG gilt höchstens {debug_max_minutes} Minuten "
            "(personenbezogene Daten, Log-Volumen)"
        )
    return expiry


def _active(now: datetime):
    return or_(LogLevelSetting.expires_at.is_(None), LogLevelSetting.expires_at > now)


async def list_active(session: AsyncSession, now: datetime) -> list[LogLevelSetting]:
    result = await session.execute(
        select(LogLevelSetting).where(_active(now)).order_by(LogLevelSetting.scope)
    )
    return list(result.scalars().all())


def distributed(settings_rows: list[LogLevelSetting]) -> dict:
    """The form every replica receives (see `dms_common.logging`)."""
    config: dict = {"global": None, "services": {}, "loggers": []}
    for row in settings_rows:
        scope = Scope.parse(row.scope)
        if scope.logger:
            config["loggers"].append(
                {"service": scope.service, "logger": scope.logger, "level": row.level}
            )
        elif scope.service:
            config["services"][scope.service] = row.level
        else:
            config["global"] = row.level
    return config


async def set_level(
    session: AsyncSession,
    scope: Scope,
    level: str,
    *,
    actor: str,
    expires_at: datetime | None,
    now: datetime,
) -> tuple[LogLevelSetting, dict | None]:
    """Upsert; returns the row and the previous state (for the audit event)."""
    row = await session.get(LogLevelSetting, scope.key)
    previous = None
    if row is None:
        row = LogLevelSetting(scope=scope.key)
        session.add(row)
    else:
        previous = {"level": row.level, "expires_at": _iso(row.expires_at)}
    row.level = level
    row.expires_at = expires_at
    row.updated_by = actor
    row.updated_at = now
    await session.flush()
    return row, previous


async def remove(session: AsyncSession, scope: Scope) -> dict | None:
    row = await session.get(LogLevelSetting, scope.key)
    if row is None:
        return None
    previous = {"level": row.level, "expires_at": _iso(row.expires_at)}
    await session.delete(row)
    await session.flush()
    return previous


async def purge_expired(session: AsyncSession, now: datetime) -> list[dict]:
    """Deletes expired entries and returns them. With several replicas only
    one gets each row back (`DELETE … RETURNING`), so each expiry is
    announced once."""
    result = await session.execute(
        delete(LogLevelSetting)
        .where(LogLevelSetting.expires_at.is_not(None), LogLevelSetting.expires_at <= now)
        .returning(LogLevelSetting.scope, LogLevelSetting.level, LogLevelSetting.expires_at)
    )
    return [
        {"scope": scope, "level": level, "expires_at": _iso(expires)}
        for scope, level, expires in result.all()
    ]


def effective(config: dict, services: list[str]) -> dict[str, dict]:
    """What applies per service, and where it comes from - the same
    resolution the replicas run (`dms_common.logging.resolve_log_levels`).
    `root_source` `env` means the service's own DMS_LOG_LEVEL."""
    view: dict[str, dict] = {}
    for service in services:
        root, loggers = resolve_log_levels(config, service)
        if service in (config.get("services") or {}):
            source = "service"
        elif config.get("global"):
            source = "global"
        else:
            source = "env"
        own = {e["logger"] for e in config.get("loggers") or [] if e.get("service") == service}
        view[service] = {
            "root_level": root,
            "root_source": source,
            "loggers": {
                name: {"level": level, "source": "service" if name in own else "all"}
                for name, level in sorted(loggers.items())
            },
        }
    return view


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
