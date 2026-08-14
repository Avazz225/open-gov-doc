from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("permission")


class ResourceNode(Base):
    """Generic node of a resource hierarchy (currently: folders).

    Not created by this service itself (with the exception of the root
    node), but kept in sync via structure events from the respective owner
    service (Folder Service, P3-S3) - see ``structure_consumer.py``.
    """

    __tablename__ = "resource_node"

    resource_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("permission.resource_node.resource_id"), nullable=True
    )
    resource_type: Mapped[str] = mapped_column(String(64), default="folder")
    # Inheritance on/off (4.1) - False breaks the inheritance chain at this node.
    inherit: Mapped[bool] = mapped_column(Boolean, default=True)


class Role(Base):
    __tablename__ = "role"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str] = mapped_column(String(512), default="")
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)


class RoleAssignment(Base):
    __tablename__ = "role_assignment"
    __table_args__ = (
        UniqueConstraint(
            "principal_type", "principal_id", "role_id", "resource_id", name="uq_assignment"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    principal_type: Mapped[str] = mapped_column(String(16))  # "user" | "group"
    principal_id: Mapped[str] = mapped_column(String(128), index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("permission.role.id"))
    resource_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("permission.resource_node.resource_id"), index=True
    )


class ScopeLock(Base):
    """Scope lock (4.7): temporarily locks a resource subtree for regular
    users, independent of the otherwise applicable RBAC rights - a standalone
    construct that overlays RBAC rather than modifying it. Never hard
    deleted (``released_at``/``released_by`` document the release), so the
    history remains auditable (5.3)."""

    __tablename__ = "scope_lock"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    resource_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("permission.resource_node.resource_id"), index=True
    )
    locked_by: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # False (default) blocks only write access, True also blocks read access.
    blocks_read: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ApprovalActionConfig(Base):
    """Four-eyes configuration per action type (4.3): "configurable per
    action type, not globally enforced" - if a row is missing for an action
    type, ``requires_approval=False`` applies implicitly (see
    ``repository.get_approval_config``)."""

    __tablename__ = "approval_action_config"

    action_type: Mapped[str] = mapped_column(String(128), primary_key=True)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    # Optional tightening of 4.3 into 4.6 (break-glass): if set, both the
    # initiator and the approver must hold this capability at the root
    # resource (in addition to the initiator != approver rule) - without
    # this, "any second person" (4.3) would be too weak for "two different
    # members of a permission group" (4.6).
    required_permission: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApprovalRequest(Base):
    """Generic approval request (4.3): an action with the four-eyes flag
    active creates a row here instead of being executed immediately. After
    approval, this service does NOT execute the actual action itself, but
    publishes ``permission.approval.approved`` - the initiating service (or
    this service itself as a consumer of its own event, see
    ``approval_consumer.py``) executes the action based on ``payload``."""

    __tablename__ = "approval_request"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    action_type: Mapped[str] = mapped_column(String(128), index=True)
    initiated_by: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rejected_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EffectivePermissionCache(Base):
    """Materialized cache (4.1) - fully cleared on every permission/structure
    change (see repository.invalidate_cache) instead of being invalidated
    granularly per affected subtree. A deliberate simplification for the
    initial version; the result stays correct, only the rebuild after a
    change is somewhat broader than strictly necessary.
    """

    __tablename__ = "effective_permission_cache"

    principal_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    roles: Mapped[list[str]] = mapped_column(JSON)
    permissions: Mapped[list[str]] = mapped_column(JSON)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Delegation(Base):
    """Delegation during absence (4.4a, P14-S11) - time-limited,
    scope-limited transfer of task handling from an absent person
    (``delegator_principal_id``) to a deputy (``deputy_principal_id``).
    Deliberately NOT an identity switch: the deputy continues to act under
    their own account - this record is only the basis for the permission
    check (``GET /delegations/check``, called by workflow-service when
    completing a task) and the "on behalf of" audit note (5.3), not a login
    mechanism. Never hard deleted (``revoked_at``/``revoked_by`` document the
    early termination), same pattern as ``ScopeLock`` above and
    document-service's ``ShareLink`` (P14-S10)."""

    __tablename__ = "delegation"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    delegator_principal_id: Mapped[str] = mapped_column(String(128), index=True)
    deputy_principal_id: Mapped[str] = mapped_column(String(128), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # None = no restriction on this dimension - if all three are None, the
    # "full takeover of all open tasks" (4.4a) applies. `scope_process_
    # definition_ids` is the only one of the concept-wording dimensions that
    # can actually be checked by today's consumer (workflow-service) without
    # an additional cross-service detour (process instances already carry
    # `process_definition_id` directly) - the other two fields are still
    # stored (concept wording "object types ... folder areas"), even though
    # they are not currently evaluated by any endpoint (see ADR 0048, "Open
    # Points").
    scope_object_type_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    scope_process_definition_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    scope_folder_resource_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class Group(Base):
    """Admin-creatable groups (Post-Roadmap Phase 22 Session 2) - complement
    the hard-coded "everyone" group that has existed since Phase 19 Session 2
    (see ``repository.EVERYONE_*``) with real, admin-managed groups with
    explicit membership (``GroupMembership``). A role assignment to one of
    these groups (``RoleAssignment.principal_type="group"``,
    ``principal_id=<group.id>``) applies to every enrolled member, without
    having to assign the role to each individually - unlike "everyone"
    (implicit membership, no own row), every real group needs explicit
    ``GroupMembership`` rows, see ``repository._collect_effective_roles``.
    """

    __tablename__ = "group"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GroupMembership(Base):
    __tablename__ = "group_membership"
    __table_args__ = (UniqueConstraint("group_id", "principal_id", name="uq_group_membership"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("permission.group.id"), index=True
    )
    principal_id: Mapped[str] = mapped_column(String(128), index=True)


class SystemMaintenanceMode(Base):
    """System-wide emergency lock & maintenance mode (4.8, P6-S6) - singleton
    (fixed ``id=1``, same pattern as ``OcrConfig``/``GuardConfig`` in other
    services). Never deleted, only toggled - ``triggered_by``/``lifted_by``
    remain in place as an audit trail even after being lifted, until the
    next activation."""

    __tablename__ = "system_maintenance_mode"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lifted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lifted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
