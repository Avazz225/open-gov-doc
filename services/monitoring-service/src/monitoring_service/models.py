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


class TracingConfig(Base):
    """The tracing sampling ratio (P94-S1, ADR 0250): one row (`id=1`), handed
    out with the sensor configuration that every service polls. Absent = the
    monitoring-service's own `DMS_OTEL_SAMPLING_RATIO` (Helm/compose)."""

    __tablename__ = "tracing_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sampling_ratio: Mapped[float] = mapped_column(Float)
    updated_by: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LogLevelSetting(Base):
    """A central log level (P97-S1, ADR 0267). `scope` is `global`,
    `service:<service>` or `logger:<service|*>:<logger>`; handed out with the
    sensor configuration every service polls. An entry past `expires_at` no
    longer applies and is removed by the log-level loop."""

    __tablename__ = "log_level_setting"

    scope: Mapped[str] = mapped_column(String(512), primary_key=True)
    level: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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


class VulnerabilityDatabase(Base):
    """One imported OSV database per ecosystem (P100-S3, ADR 0272): what an
    offline scan checks against. `covered` holds the package names installed at
    import time - only their records are kept, so a package added later is
    reported as not covered instead of silently clean."""

    __tablename__ = "vulnerability_database"

    ecosystem: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_name: Mapped[str] = mapped_column(String(512))
    record_count: Mapped[int] = mapped_column(Integer)
    covered: Mapped[list] = mapped_column(JSON, default=list)
    # The newest `modified` among the kept records: how current the data is.
    # OSV still serves stale dumps (e.g. `Debian:13/all.zip` from 2024).
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    imported_by: Mapped[str] = mapped_column(String(128))


class VulnerabilityRecord(Base):
    """An OSV record kept from an import, per ecosystem."""

    __tablename__ = "vulnerability_record"

    ecosystem: Mapped[str] = mapped_column(String(64), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    record: Mapped[dict] = mapped_column(JSON)


class VulnerabilityPackage(Base):
    """Which records name which installed package (lookup for a scan)."""

    __tablename__ = "vulnerability_package"

    ecosystem: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(128), primary_key=True)


class VulnerabilityAlias(Base):
    """Every id a kept record goes by (lookup for a single-id check offline)."""

    __tablename__ = "vulnerability_alias"

    alias: Mapped[str] = mapped_column(String(128), primary_key=True)
    ecosystem: Mapped[str] = mapped_column(String(64), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(128), primary_key=True)


class VulnerabilityImport(Base):
    """An import job: dumps are large, so the import runs in the background."""

    __tablename__ = "vulnerability_import"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))  # running | done | failed
    source_name: Mapped[str] = mapped_column(String(512))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actor: Mapped[str] = mapped_column(String(128))
    ecosystems: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)


class VulnerabilityScan(Base):
    """One run over the whole inventory and its result."""

    __tablename__ = "vulnerability_scan"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))  # running | done | failed
    source: Mapped[str] = mapped_column(String(16))  # online | offline
    trigger: Mapped[str] = mapped_column(String(16))  # manual | schedule
    actor: Mapped[str] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    vulnerabilities: Mapped[list | None] = mapped_column(JSON, nullable=True)
    uncovered: Mapped[list | None] = mapped_column(JSON, nullable=True)
    checked_services: Mapped[int] = mapped_column(Integer, default=0)
