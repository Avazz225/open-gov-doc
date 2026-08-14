from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("fleet")


class ManagedInstallation(Base):
    """A fully independent installation (3a) overseen by the operator of this
    fleet-management-service. Unlike `federation-hub-service`'s
    ``Installation`` (which only stores a hash, since the hub never needs the
    plaintext key again), this table stores ``fleet_agent_api_key`` in
    **plaintext** - this service must PRESENT the key on every outgoing call
    to the installation, never verify it itself (identical reasoning to
    `migration-service`'s ``PairedInstallation``, see there)."""

    __tablename__ = "managed_installation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256))
    gateway_base_url: Mapped[str] = mapped_column(String(512))
    fleet_agent_api_key: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InstallationGroup(Base):
    """A named group/"wave" (3a extension, P13-S2b: "installations are
    grouped into named groups") - pure labeling, no execution logic of its
    own. An installation can be a member of multiple groups (e.g.
    "test installations" AND "customer-north cluster")."""

    __tablename__ = "installation_group"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InstallationGroupMember(Base):
    __tablename__ = "installation_group_member"

    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("fleet.installation_group.id"), primary_key=True
    )
    installation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("fleet.managed_installation.id"), primary_key=True
    )


class UpdatePlan(Base):
    """Declarative, versioned update plan (3a: "a structured sequence of
    named steps ... the plan is versioned ... not code"). ``steps`` is an
    ordered JSON list of ``{name, step_type, requires_approval}`` -
    ``step_type`` is either ``"verify"`` (automatic, see
    `main._run_verify_step`) or ``"gate"`` (confirmed via an explicit
    `POST .../mark-done` - stands for any step whose actual execution lies
    outside this service: setting a scope lock/maintenance mode (4.7/4.8),
    performing the actual rolling update (10.5), taking a backup (10.4),
    final approval). See ADR 0038 for the reasoning behind this deliberate
    boundary - this service does not itself trigger these actions on a
    foreign installation, it only coordinates WHEN/IN WHICH ORDER they
    should happen and tracks the outcome."""

    __tablename__ = "update_plan"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    version: Mapped[str] = mapped_column(String(32))
    steps: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Rollout(Base):
    """A wave: an `UpdatePlan` applied to a set of installations (group
    members ∪ ``include`` − ``exclude``, 3a: "installations can be
    deliberately included/excluded"). Must be started explicitly
    (``POST /rollouts/{id}/start``) - no automatic chain reaction to the next
    wave, which is its own, separately created/started `Rollout` (3a: "the
    next wave only starts after confirmation of the previous one")."""

    __tablename__ = "rollout"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("fleet.update_plan.id"))
    name: Mapped[str] = mapped_column(String(256))
    group_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("fleet.installation_group.id"), nullable=True
    )
    include: Mapped[list] = mapped_column(JSON, default=list)
    exclude: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_by: Mapped[str | None] = mapped_column(String(256), nullable=True)


class InstallationRun(Base):
    """Progress of a single installation within a `Rollout` (3a: "error
    class, current step, and progress per installation"). ``status`` holds
    either ``"pending"``/``"completed"`` or one of the five concept error
    decisions verbatim (``retry_later``/``wait_external``/
    ``manual_required``/``recoverable_failed``/``fatal_contract``) - see
    `orchestration.py`."""

    __tablename__ = "installation_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rollout_id: Mapped[str] = mapped_column(String(36), ForeignKey("fleet.rollout.id"))
    installation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("fleet.managed_installation.id")
    )
    current_step_index: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32))
    last_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Only populated for ``requires_approval`` steps - who proposed the step
    # as done, so `approve()` can enforce that a DIFFERENT person approves it
    # (structural rather than cryptographic separation between
    # proposal/approval, see ADR 0038 - fleet-management-service does not
    # manage its own users).
    proposed_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
