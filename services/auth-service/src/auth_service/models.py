from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("auth")


class FederationIdentity(Base):
    """Own federation identity for the optional federated contact directory
    search (2.5/7.4, P15-S4) - this service's first own Postgres schema
    ever (previously stateless, see README/docs/services/
    auth-service.md), deliberately justified by exactly one singleton row
    (``id=1``, same pattern as `workflow_service.FederationIdentity`).

    Registers as its OWN entry in the same address book, independent of
    `workflow-service`'s Federation Hub participation (fresh `uuid4()`
    instead of a shared `installation_id`, same generation pattern as
    there) - the Hub only knows "installation" as a generic address book
    entry that can be registered independently by any service, no
    hardwired 1:1 mapping to a specific installation. Two entries (one for
    workflow federation, one for contact directory search) per physical
    installation is a deliberate, documented simplification compared to a
    single, cross-service shared identity - see ADR 0054 "Consequences"."""

    __tablename__ = "federation_identity"

    id: Mapped[int] = mapped_column(primary_key=True)
    installation_id: Mapped[str] = mapped_column(String(128))
    private_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    public_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SsoConfig(Base):
    """SSO/automatic login (post-roadmap feature) - installation-wide
    switch, same single-row pattern as document-service's
    `ShareLinkConfig`. `enabled=False` (default) means: `login/page.tsx`
    continues to show the password form unchanged, no automatic redirect
    to Keycloak."""

    __tablename__ = "sso_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LocalSigningKey(Base):
    """Auth decoupling from Keycloak (post-roadmap feature, Phase 18, ADR
    0063) - own RSA key pair for tokens of technical accounts
    (superuser/domain admins), issued independently of Keycloak.
    Singleton row (`id=1`), same pattern as `FederationIdentity` above.
    `kid` is the `kid` claim in the token header - stable across restarts
    (no new key on every startup), otherwise already-issued, still-valid
    tokens would suddenly find no matching key in the JWKS anymore."""

    __tablename__ = "local_signing_key"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kid: Mapped[str] = mapped_column(String(64))
    private_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    public_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TechnicalAccount(Base):
    """Auth decoupling from Keycloak (post-roadmap feature, Phase 18, ADR
    0063) - superuser and domain admin accounts live exclusively here from
    this phase on, no longer as Keycloak user accounts. `password_hash` is
    a bcrypt hash (the first time this service hashes a password itself -
    previously Keycloak handled that entirely). `role_name` is the role to
    be submitted to `permission-service` (e.g. `domain-admin-users`),
    identical to the previous Keycloak account pattern - `NULL` for the
    superuser (P18-S2), whose special privileges don't run through a
    permission-service role but via direct name comparison at several
    points in the system (e.g. emergency shutdown, query-service's
    `_is_active_superuser`). `enabled`/`expires_at` continue to carry the
    break-glass semantics for the superuser (4.6) - now purely app-side,
    without a Keycloak attribute."""

    __tablename__ = "technical_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    account_type: Mapped[str] = mapped_column(String(32))
    role_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AdGroupRoleMapping(Base):
    """Configurable AD/Keycloak group -> internal role mapping (4.4,
    P24-S2). OWN, lean table instead of reusing
    `permission_service.models.Group`/`GroupMembership`/`RoleAssignment`
    (post-roadmap Phase 22) - those model ADMIN-CREATED groups with an
    explicit, synchronously maintained membership table, a different,
    independent function. This mapping instead maps EXTERNAL Keycloak/AD
    group claims (`groups` JWT claim, see
    `bootstrap._ensure_groups_mapper`) onto internal role names - no own
    membership table needed, since the claim comes fresh from Keycloak on
    every token acquisition anyway and is evaluated dynamically on every
    `/me` request (see `ad_group_mapping.resolve_roles_for_groups`).

    Deliberate scope cut for this session (see docs/services/auth-service.md
    "Open Points" and ADR 0093): only simple 1:1 mapping (one
    `ad_group_name` -> one `role_name`), no composite rules (group AND
    attribute, multiple groups -> one role via AND logic), which concept
    4.4 describes as the full target scope. An AD group name can occur
    multiple times (e.g. mapped to two different roles) -
    `UniqueConstraint` only prevents exact duplicate rows."""

    __tablename__ = "ad_group_role_mapping"
    __table_args__ = (
        UniqueConstraint("ad_group_name", "role_name", name="uq_ad_group_role_mapping"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ad_group_name: Mapped[str] = mapped_column(String(255), index=True)
    role_name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
