import uuid
from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("teamspace")


class Teamspace(Base):
    """Self-managed, persistent team workspace (2.5, P14-S6) -
    deliberately distinct from the circulation folder (2.3,
    `case-service`): a circulation folder is a sequential handoff with a
    completion, a teamspace is a persistent group work area with no
    defined end. Any authenticated principal can create a new teamspace
    (no administrative pre-setup needed, per the literal wording of
    concept 2.5) - `repository.create_teamspace` automatically creates a
    dedicated root folder in `folder-service` (`root_folder_id`, an
    opaque reference, no FK across service boundaries) and makes the
    creating person the first member with `can_manage_members=True`."""

    __tablename__ = "teamspace"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    root_folder_id: Mapped[str] = mapped_column(String(128))
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TeamspaceMember(Base):
    """Membership (2.5, P14-S6) - the actual access regime of this
    service, independent of the rest of the RBAC (4.1): every endpoint
    except creating a new teamspace requires an existing membership row
    for the calling `X-DMS-Principal` (see `main.py._require_member`).
    `can_manage_members` deliberately covers both member management
    (invite/remove) and deleting the entire teamspace - ONE flag instead
    of two separate permission levels, since concept 2.5 itself makes no
    distinction between the two ("but this management can be delegated to
    other members"). Deliberate boundary: `principal_id` is exclusively a
    single user, NOT a group principal - "group" is not a really enforced
    concept anywhere in the project (neither in Keycloak nor in
    `permission-service`'s `RoleAssignment.principal_type`, where it's
    only an unused comment value), see `docs/services/
    teamspace-service.md` "Deliberate boundaries"."""

    __tablename__ = "teamspace_member"
    __table_args__ = (UniqueConstraint("teamspace_id", "principal_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    teamspace_id: Mapped[str] = mapped_column(ForeignKey("teamspace.teamspace.id"), index=True)
    principal_id: Mapped[str] = mapped_column(String(255), index=True)
    can_manage_members: Mapped[bool] = mapped_column(Boolean, default=False)
    invited_by: Mapped[str] = mapped_column(String(255))
    invited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # AD-group invitation (2.5, Post-Roadmap Phase 74 Session 3, ADR
    # 0160/ADR 0217) - `NULL` for a manually-invited member (the only kind
    # that existed before this session, unaffected by any group
    # reconciliation). Set to the owning `TeamspaceAdGroupBinding.
    # ad_group_name` when `_ad_group_reconciliation_poll_loop` (or the
    # binding's own initial sync) creates this row instead of a manager's
    # own `POST .../members` call - the poll loop only ever adds/removes
    # rows it itself attributed this way, never touching a manually-
    # invited member even if that same person also happens to be in the
    # bound AD group (see `main.py`'s reconciliation logic). A person can
    # still only ever have ONE membership row per teamspace (the existing
    # `UniqueConstraint` above), so a manual invite always wins if it
    # already exists - the reconciler skips creating a duplicate.
    source_ad_group_name: Mapped[str | None] = mapped_column(String(256), default=None)


class TeamspaceAdGroupBinding(Base):
    """Records that a teamspace is bound to a Keycloak/AD group for
    ongoing membership synchronization (2.5, Post-Roadmap Phase 74
    Session 3, ADR 0160/ADR 0217) - analogous in spirit to
    `TeamspaceMember` but for the group binding itself, not a snapshot of
    its members. Deliberately live/poll-reconciled, never a one-time
    member-list copy (see `main.py._ad_group_reconciliation_poll_loop`) -
    the same "Keycloak/AD remains the sole source of truth, never
    mirrored" principle ADR 0093 already established for AD-group->role
    mapping, extended here to a second feature area per ADR 0160's own
    explicit recommendation."""

    __tablename__ = "teamspace_ad_group_binding"
    __table_args__ = (UniqueConstraint("teamspace_id", "ad_group_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    teamspace_id: Mapped[str] = mapped_column(ForeignKey("teamspace.teamspace.id"), index=True)
    ad_group_name: Mapped[str] = mapped_column(String(256), index=True)
    invited_by: Mapped[str] = mapped_column(String(255))
    invited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TeamspaceAppointment(Base):
    """Shared appointment (2.5, P14-S6) - a completely new concept, no
    reuse of `workflow-service`'s SLA business calendars (P14-S5, which
    only does business-day arithmetic for deadline calculation, no user-
    visible appointments). Deliberately fully shared (no creator-
    exclusive editing/deletion) - every member may create/delete every
    appointment, matching the concept wording "shared appointments"
    without mention of individual ownership."""

    __tablename__ = "teamspace_appointment"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    teamspace_id: Mapped[str] = mapped_column(ForeignKey("teamspace.teamspace.id"), index=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TeamspaceContact(Base):
    """Shared contact (2.5, P14-S6) - deliberately a simple, free-form
    address-book entry per teamspace, NOT the future, installation-wide
    contacts special area from concept 2.5 (that will be a directory of
    the organization's own staff based on `auth-service`, Phase 15, not
    yet built) - per the concept table, the two are clearly separate
    special areas with different purposes (installation-wide directory of
    people vs. teamspace-local free-form notes, e.g. also for external
    contacts outside the team)."""

    __tablename__ = "teamspace_contact"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    teamspace_id: Mapped[str] = mapped_column(ForeignKey("teamspace.teamspace.id"), index=True)
    name: Mapped[str] = mapped_column(String(256))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
