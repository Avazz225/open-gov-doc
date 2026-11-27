from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SensorOut(BaseModel):
    """An entry of the sensor registry (10.1) - aggregated from the
    self-declarations of all instances currently known to `registry-service`,
    enriched with the resolved activation status."""

    name: str
    group: str
    # 1 (very cheap) to 5 (very expensive), P88-S2c; registrations from
    # older service images that still say cheap/expensive are normalized.
    cost: int
    description: str
    service_types: list[str]
    active: bool
    # P88-S2c: a sensor declared off by default stays off under "monitor
    # everything" until an override switches it on.
    default_enabled: bool = True
    # P88-S2 (ADR 0243): which setting decided `active` - the sensor's own
    # override, its group's, its cost class's, or the global default.
    source: Literal["sensor", "group", "cost", "global", "default"] = "global"


class SensorConfigOut(BaseModel):
    global_default: bool
    overrides: dict[str, bool]
    # P94-S1 (ADR 0250): share of new traces that are recorded, 0..1.
    tracing_sampling_ratio: float | None = None
    # P97-S1 (ADR 0267): central log levels in the form every replica
    # applies (`dms_common.logging.apply_log_levels`); expired ones left out.
    log_levels: dict | None = None


class ManualVulnerability(BaseModel):
    ecosystem: Literal["PyPI", "npm", "Debian", "Alpine"]
    package: str = Field(min_length=1, max_length=200)
    introduced: str | None = None
    fixed: str | None = None
    last_affected: str | None = None
    label: str | None = Field(default=None, max_length=100)


class VulnerabilityCheckIn(BaseModel):
    """Exactly one of: an id to look up online, an OSV record (or a list of
    them) from a file, or a hand-entered package with its affected range."""

    id: str | None = Field(default=None, max_length=100)
    record: dict | list[dict] | None = None
    manual: ManualVulnerability | None = None


class DeadLetterOut(BaseModel):
    """A message a consumer gave up on (P99-S2, ADR 0270)."""

    seq: int
    stream: str
    consumer: str
    subject: str
    stream_seq: str
    deliveries: int
    error: str
    failed_at: str
    replayed_from: str | None = None
    size: int


class DeadLetterDetailOut(DeadLetterOut):
    # The original event, as text (JSON for every event of this platform);
    # cut at 64 KB.
    payload: str
    payload_truncated: bool = False


class DeadLetterAction(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class LogLevelIn(BaseModel):
    """No service and no logger = global; a service = that service's root
    level; a logger with a service (or `*`, or none = `*`) = that logger."""

    service: str | None = None
    logger: str | None = None
    level: str
    expires_in_minutes: int | None = None
    expires_at: datetime | None = None


class LogLevelSettingOut(BaseModel):
    scope: str
    service: str | None
    logger: str | None
    level: str
    expires_at: datetime | None
    updated_by: str
    updated_at: datetime


class LogLevelsOut(BaseModel):
    settings: list[LogLevelSettingOut]
    # Per service: root level and source (`service`/`global`/`env` = the
    # service's own DMS_LOG_LEVEL), and the logger levels that apply.
    effective: dict[str, dict]
    # Logger names per service, from the instances' registrations.
    catalog: dict[str, list[str]]
    debug_default_minutes: int
    debug_max_minutes: int


class TracingConfigOut(BaseModel):
    sampling_ratio: float
    updated_by: str | None = None
    updated_at: datetime | None = None
    # Whether this monitoring-service itself exports traces (endpoint set).
    tracing_enabled: bool = False


class TracingConfigIn(BaseModel):
    sampling_ratio: float = Field(ge=0.0, le=1.0)


class GlobalSensorConfigIn(BaseModel):
    enabled: bool


class SensorOverrideIn(BaseModel):
    """`{sensor_name}` in the path is a sensor name, `group:<group>` or
    `cost:<1-5>` (P88-S2/S2c, ADR 0243). The legacy `cost:cheap`/
    `cost:expensive` are accepted and written as levels 1-3/4-5."""

    # `None` deletes an existing override again - the sensor then
    # falls back to the global base setting.
    enabled: bool | None


# --- Alerting (P88-S5, ADR 0245) -------------------------------------------------

AlertAggregation = Literal["max", "min", "sum", "avg", "rate_per_minute"]
AlertLevel = Literal["ok", "warning", "error", "unknown"]


class AlertRuleIn(BaseModel):
    name: str
    sensor: str
    label_filter: dict[str, str] = {}
    aggregation: AlertAggregation = "max"
    min_error: float | None = None
    min_warning: float | None = None
    max_warning: float | None = None
    max_error: float | None = None
    warning_repeat: int = 1
    error_repeat: int = 1
    enabled: bool = True
    description: str | None = None


class AlertRuleOut(AlertRuleIn):
    id: str
    updated_at: datetime
    updated_by: str | None
    # The evaluated state; `level` "unknown" before the first evaluation.
    level: AlertLevel = "unknown"
    value: float | None = None
    since: datetime | None = None
    evaluated_at: datetime | None = None
    notified_level: AlertLevel | None = None
    suppressed: bool = False


class DowntimeWindowIn(BaseModel):
    name: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    weekdays: list[int] | None = None
    daily_start: str | None = None
    daily_end: str | None = None
    rule_ids: list[str] | None = None
    enabled: bool = True


class DowntimeWindowOut(DowntimeWindowIn):
    id: str
    active: bool
    updated_at: datetime
    updated_by: str | None


class AlertSeriesOut(BaseModel):
    labels: dict[str, str]
    value: float


class AlertPreviewOut(BaseModel):
    """P88-S6: a draft rule evaluated against a fresh scrape, without
    persisting anything - the current value as a reference while editing."""

    value: float | None
    level: AlertLevel
    series: list[AlertSeriesOut]
    # Why there is no value: "no_samples", "rate_needs_history".
    reason: str | None = None
