from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("monitoring")

GLOBAL_DEFAULT_KEY = "__global__"


class SensorConfigEntry(Base):
    """Sensor activation configuration (10.1, P11-S1) - a special key
    `__global__` holds the base setting ("monitor everything"/"nothing"),
    every other key is a sensor-specific override that overrides the
    base setting for exactly that sensor. Deliberately no
    connection to 7.3 (configuration export only exists from P12-S3, see
    P11-S0 finding) - standalone, audited persistence until then."""

    __tablename__ = "sensor_config"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)


class AlertRule(Base):
    """A threshold rule on one sensor (P88-S5, ADR 0245 Decision 2)."""

    __tablename__ = "alert_rule"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    sensor: Mapped[str] = mapped_column(String(255), index=True)
    label_filter: Mapped[dict] = mapped_column(JSON, default=dict)
    aggregation: Mapped[str] = mapped_column(String(32), default="max")
    min_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_warning: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_warning: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    warning_repeat: Mapped[int] = mapped_column(Integer, default=1)
    error_repeat: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class AlertState(Base):
    """The evaluated state of one rule (Decision 3) - streaks, the last
    total for rates, and what was last notified."""

    __tablename__ = "alert_state"

    rule_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    level: Mapped[str] = mapped_column(String(16), default="unknown")
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    warning_streak: Mapped[int] = mapped_column(Integer, default=0)
    error_streak: Mapped[int] = mapped_column(Integer, default=0)
    last_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_total_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notified_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)


class DowntimeWindow(Base):
    """A maintenance window that suppresses notification (Decision 5)."""

    __tablename__ = "downtime_window"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    weekdays: Mapped[list | None] = mapped_column(JSON, nullable=True)
    daily_start: Mapped[str | None] = mapped_column(String(5), nullable=True)
    daily_end: Mapped[str | None] = mapped_column(String(5), nullable=True)
    rule_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
