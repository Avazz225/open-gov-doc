from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("workflow")


class ProcessDefinition(Base):
    """An imported BPMN 2.0 process definition (2.2/7.1, P6-S1). Imported via
    BPMN XML upload, since P6-S8 also from the standalone Process
    Designer (`apps/process-designer`). Since P6-S8, ``name`` is the
    process family key, no longer globally unique - uniqueness now
    applies to ``(name, version)``: a repeat upload under the same
    ``name`` automatically creates the next version (see
    `repository.create_process_definition`) instead of being rejected
    with 409. A process instance always binds to a concrete, immutable
    version via ``process_definition_id`` - later new versions of the
    same family never retroactively affect already-running instances."""

    __tablename__ = "process_definition"
    __table_args__ = (
        UniqueConstraint("name", "version", name="ux_process_definition_name_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256))
    version: Mapped[int] = mapped_column(Integer, default=1)
    # The internal process ID from the BPMN XML itself (`<bpmn:process id="...">"`),
    # not the display name assigned here - needed again for every
    # `spiff_adapter.parse_bpmn` call at instance start.
    bpmn_process_id: Mapped[str] = mapped_column(String(256))
    bpmn_xml: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DmnDefinition(Base):
    """An imported DMN 1.3 decision table (7.1, P14-S4). Same versioning
    pattern as ``ProcessDefinition`` (ADR 0027): ``name`` is the family
    key, a repeat upload under the same name automatically creates the
    next version.

    ``decision_id`` is the internal ``<decision id="...">`` from the DMN
    XML itself (extracted via ``spiff_adapter.parse_dmn``) - this is the
    actual key that a ``bpmn:businessRuleTask``'s ``camunda:decisionRef``
    references it by, not ``name`` (see `spiff_adapter.py`). Must be
    unique among the respective NEWEST versions of all families
    (`repository.create_dmn_definition`) - for every BPMN parse,
    SpiffWorkflow always loads only the newest version of each family
    into the same parser (see `repository.list_latest_dmn_xml`); two
    families with a colliding ``decision_id`` would no longer be
    distinguishable there."""

    __tablename__ = "dmn_definition"
    __table_args__ = (UniqueConstraint("name", "version", name="ux_dmn_definition_name_version"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256))
    version: Mapped[int] = mapped_column(Integer, default=1)
    decision_id: Mapped[str] = mapped_column(String(256))
    dmn_xml: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BusinessCalendar(Base):
    """Regional business calendar for SLA deadline calculation (7.1, P14-S5) -
    a named, freely maintainable list of non-working days (holidays) that a
    timer duration in a BPMN file can reference via the new `business_days(n,
    calendar_name)` function (see `spiff_adapter.py`). Weekends (Sat/Sun)
    ALWAYS count as non-working regardless of a specific calendar -
    `non_working_dates` contains only the ADDITIONAL, calendar-specific
    days (holidays). Deliberately NO versioning pattern, unlike
    `ProcessDefinition`/`DmnDefinition`: a calendar is continuously
    maintained reference data (e.g. adding new holidays each year), not
    immutable, versioned execution logic - normal update-in-place like
    `SensorConfig`/`ApprovalConfig`.

    `is_default`: at most ONE calendar may be `True` at a time
    (`repository.py` resets all others when a new default calendar is
    set) - used by `business_days(n)` WITHOUT an explicit `calendar_name`
    argument (installation-wide default, see Concept 7.1: "assignable to
    a process/an installation" - a calendar explicitly named in the BPMN
    file itself is the process assignment, this default is the
    installation assignment)."""

    __tablename__ = "business_calendar"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), unique=True)
    non_working_dates: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProcessInstance(Base):
    """A running or completed execution of a process definition.

    ``workflow_state`` is the complete JSON blob produced by
    ``spiff_adapter.serialize()`` (ADR 0019: no separate, normalized task
    model - SpiffWorkflow already has the correct BPMN execution
    semantics, this module treats the blob as opaque and never touches it
    directly, only through ``spiff_adapter``).

    ``business_key``, like ``folder_id``/``object_type_id`` in other
    services, is an opaque cross-service reference (e.g. a future
    ``document_id``) without FK enforcement across service boundaries -
    unlike there, as of P6-S1 no caller actually checks it against
    another service yet."""

    __tablename__ = "process_instance"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    process_definition_id: Mapped[int] = mapped_column(
        ForeignKey("workflow.process_definition.id"), index=True
    )
    business_key: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16))  # "running" | "completed"
    workflow_state: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FederationIdentity(Base):
    """This installation's own federation identity (7.4, P6-S9) -
    deliberately a single row with fixed ``id=1``, same singleton pattern
    as `signature-service`'s `InternalCa`. Created on the first start with
    a configured `Settings.federation_hub_base_url` (own RSA-2048 key
    pair, see `federation_crypto.py`) and reused idempotently thereafter -
    other installations encrypt payloads for us with `public_key_pem`,
    `private_key_pem` decrypts them again. Since P13-S4 (ADR 0039), the
    same key pair additionally serves as the cryptographic identity
    towards the Hub: every own call is signed with `private_key_pem`
    (`X-Installation-Signature`) instead of - as before - sending a Hub-
    issued `api_key` as a bearer token (that field is therefore removed).
    `hub_public_key_pem` is the Hub's public key retrieved once
    (trust-on-first-use, ADR 0028), used to verify incoming deliveries
    signed by the Hub."""

    __tablename__ = "federation_identity"

    id: Mapped[int] = mapped_column(primary_key=True)
    installation_id: Mapped[str] = mapped_column(String(128))
    private_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    public_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    hub_public_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FederationConfig(Base):
    """This installation's version compatibility declaration (7.4, P13-S3) -
    deliberately its own row exportable/importable via `config-service`/7.3
    instead of a plain `Settings` field: 7.4 literally requires that this
    range be "part of the already-versioned configuration schema (7.3)"
    and "explicitly maintained with every release, not implicitly
    assumed" - before P13-S3 it lived exclusively in
    `Settings.installation_version`/`installation_min_compatible_peer_version`
    and was thus only changeable via a container restart, never reachable
    through the regular configuration import. Singleton row (``id=1``,
    same pattern as `FederationIdentity`), seeded from the `Settings`
    defaults on first access (backward-compatible)."""

    __tablename__ = "federation_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(32))
    min_compatible_peer_version: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FederationTask(Base):
    """Link between a local manual task/local instance and a handover
    running on the Hub (7.4, P6-S9) - prevents double-dispatching the same
    `federated`/`federated_return` task (see
    `main.py._dispatch_pending_federation_tasks`) and, for an incoming
    (`direction="inbound"`) handover, records which
    `origin_installation_id`/`handover_id` a later `federated_return` task
    must send its result back to. ``task_id`` is still ``None`` for a
    purely inbound row (the new instance itself, before it has reached a
    `federated_return` task).

    Uniqueness applies to ``(handover_id, direction)``, not `handover_id`
    alone: normally a single installation sees a given `handover_id` from
    only one direction anyway (either it created it itself, or it
    receives it) - **except** for the self-loopback smoke test used in
    this session (an installation hands over to itself), where the same
    `handover_id` appears in the same database as both an `outbound` and
    an `inbound` row."""

    __tablename__ = "federation_task"
    __table_args__ = (
        UniqueConstraint("handover_id", "direction", name="ux_federation_task_handover_direction"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    process_instance_id: Mapped[str] = mapped_column(
        ForeignKey("workflow.process_instance.id"), index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    handover_id: Mapped[str] = mapped_column(String(36), index=True)
    direction: Mapped[str] = mapped_column(String(16))  # "outbound" | "inbound"
    origin_installation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
