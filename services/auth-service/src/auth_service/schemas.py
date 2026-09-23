from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ThemeName = Literal["light", "dark", "high-contrast", "auto"]
# UI display language (8, Phase 47 Session 1 - ADR 0007's "a second language
# is just an additional JSON file" put into practice for the first time).
LocaleName = Literal["de", "en"]


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str


class LogoutRequest(BaseModel):
    refresh_token: str


class OidcAuthorizeOut(BaseModel):
    """SSO/automatic login (post-roadmap feature) - the client navigates to
    this URL itself, no server-side 307 redirect (consistent with this
    project's overall "service returns data, client navigates" style)."""

    authorization_url: str


class OidcCallbackRequest(BaseModel):
    code: str
    redirect_uri: str


class SsoConfigIn(BaseModel):
    enabled: bool = False


class SsoConfigOut(SsoConfigIn):
    model_config = {"from_attributes": True}

    updated_at: datetime


class UserTrackingConfigIn(BaseModel):
    """Toggle fine-grained session tracking for one principal (5.5, Post-
    Roadmap Phase 41 Session 3, ADR 0157) - `updated_by` mirrors
    `LegalHoldCreate.set_by`-style attribution fields elsewhere in this
    project (opaque, independent of the `X-DMS-Principal`-based
    `admin.user_tracking` permission check)."""

    enabled: bool
    updated_by: str


class UserTrackingConfigOut(BaseModel):
    principal_id: str
    enabled: bool
    updated_by: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserTrackingConfigActionResult(BaseModel):
    """Response envelope for `PUT /user-tracking-config/{principal_id}`
    since Post-Roadmap Phase 74 Session 2 (ADR 0157's own named, still-open
    gap: "no four-eyes on the toggle action... small fix if ever pursued,
    same pattern as ADR 0171") - same "always wrapped, regardless of
    whether approval is configured" convention as
    `AdGroupRoleMappingActionResult`."""

    status: Literal["applied", "pending_approval"]
    config: UserTrackingConfigOut | None = None
    approval_request_id: str | None = None


class UserTrackingSessionOut(BaseModel):
    id: str
    principal_id: str
    username: str
    event_type: str
    auth_method: str
    client_ip: str | None
    user_agent: str | None
    occurred_at: datetime

    model_config = {"from_attributes": True}


class UserTrackingRetentionConfigIn(BaseModel):
    retention_days: int


class UserTrackingRetentionConfigOut(UserTrackingRetentionConfigIn):
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    username: str
    email: str
    password: str
    first_name: str
    last_name: str


class UserOut(BaseModel):
    id: str
    username: str
    email: str | None
    enabled: bool
    first_name: str | None
    last_name: str | None


class UserLookupOut(BaseModel):
    """Minimal response for `GET /users/lookup` (2.5, P14-S6) - deliberately
    only `id`/`username`, see `admin_users.find_user_by_username`."""

    id: str
    username: str


class DirectoryEntryOut(BaseModel):
    """Directory entry (2.5/4.4, P15-S4) - deliberately without `enabled`
    (an account's enabled status is an administrative matter, not
    information needed for merely finding a person), otherwise identical
    field set to `UserOut`."""

    id: str
    username: str
    email: str | None
    first_name: str | None
    last_name: str | None


class FederatedDirectoryEntryOut(DirectoryEntryOut):
    """Like `DirectoryEntryOut`, extended with the originating installation
    (2.5, "cross-installation contact directory search") - necessary since
    two installations maintain independent user populations with
    potentially colliding `id`s/names."""

    installation_id: str
    installation_display_name: str


class DirectorySearchRequest(BaseModel):
    """Payload of an incoming, signed directory search request from a peer
    installation (`POST /users/directory/federated-search-inbound`)."""

    query: str


class DirectoryFederationStatusOut(BaseModel):
    enabled: bool
    peer_installation_count: int


class ThemePreference(BaseModel):
    theme: ThemeName = "auto"
    locale: LocaleName = "de"


class PreferencesUpdate(BaseModel):
    """Request body for `PUT /me/preferences` (Phase 47 Session 1) - both
    fields are optional and default to `None`, NOT to `ThemePreference`'s
    own defaults: an existing caller that only ever sent `{"theme": ...}`
    (every app before this session) must not accidentally reset the other,
    unrelated preference back to its default on every unrelated update.
    Only a field actually present in the request is written."""

    theme: ThemeName | None = None
    locale: LocaleName | None = None


class RealmRoleOut(BaseModel):
    name: str


class RealmRolesRequest(BaseModel):
    """Configuration packages (14.1, P17-S1) can bring new Keycloak realm
    roles (e.g. `dms-poststelle`, 2.5), for which there was previously no
    import path - see `bootstrap._ensure_dms_admin_role` for the same
    primitive, generalized here to arbitrary names supplied by the
    package."""

    names: list[str]


class AdGroupRoleMappingIn(BaseModel):
    """Payload for `POST /ad-group-mappings` (4.4, P24-S2) - deliberately
    only simple 1:1 mapping, see `models.AdGroupRoleMapping` docstring."""

    ad_group_name: str
    role_name: str


class AdGroupRoleMappingOut(AdGroupRoleMappingIn):
    model_config = {"from_attributes": True}

    id: int
    created_at: datetime
    created_by: str | None = None


class AdGroupRoleMappingActionResult(BaseModel):
    """Response envelope for `POST /ad-group-mappings` since Post-Roadmap
    Phase 39 Session 3 (ADR 0153, four-eyes retrofit) - same "always
    wrapped, regardless of whether approval is configured" convention as
    `permission_service.schemas.RoleActionResult`."""

    status: Literal["created", "pending_approval"]
    mapping: AdGroupRoleMappingOut | None = None
    approval_request_id: str | None = None


class AdGroupMappingApprovalStatus(BaseModel):
    """Shared response envelope for the delete endpoints of both mapping
    kinds (simple/composite) since Post-Roadmap Phase 39 Session 3 (ADR
    0153) - no resource-specific payload needed on a delete beyond the
    approval outcome itself, unlike the create envelopes above/below."""

    status: Literal["deleted", "pending_approval"]
    approval_request_id: str | None = None


class AdGroupCompositeRuleIn(BaseModel):
    """Payload for `POST /ad-group-composite-rules` (4.4, Post-Roadmap
    Phase 39 Session 3, ADR 0153) - AND-composite counterpart of
    `AdGroupRoleMappingIn`, see `models.AdGroupRoleCompositeRule`."""

    role_name: str
    ad_group_names: list[str]


class AdGroupCompositeRuleOut(BaseModel):
    id: int
    role_name: str
    ad_group_names: list[str]
    created_at: datetime
    created_by: str | None = None


class AdGroupCompositeRuleActionResult(BaseModel):
    status: Literal["created", "pending_approval"]
    rule: AdGroupCompositeRuleOut | None = None
    approval_request_id: str | None = None


class AdGroupMappingDefaultRoleIn(BaseModel):
    """Payload for `PUT /ad-group-mappings/default-role` (4.4, Post-Roadmap
    Phase 39 Session 3, ADR 0153) - `None`/omitted resets to "no default"
    (the previous hardcoded behavior)."""

    default_role_name: str | None = None


class AdGroupMappingDefaultRoleOut(BaseModel):
    model_config = {"from_attributes": True}

    default_role_name: str | None
    updated_at: datetime
    updated_by: str | None = None


class AdGroupMappingDefaultRoleActionResult(BaseModel):
    """Response envelope for `PUT /ad-group-mappings/default-role` since
    Phase 53 Session 1 (ADR 0171) - ADR 0153 deliberately left this single
    scalar setting without four-eyes ("gating it would be scope beyond what
    was asked"); this session reverses that scope boundary on explicit
    request. Same "always wrapped, regardless of whether approval is
    configured" convention as `AdGroupRoleMappingActionResult`/
    `AdGroupCompositeRuleActionResult` above - `GET` stays a plain
    `AdGroupMappingDefaultRoleOut`, unchanged."""

    status: Literal["set", "pending_approval"]
    config: AdGroupMappingDefaultRoleOut | None = None
    approval_request_id: str | None = None


class AdGroupMappingConfigBundle(BaseModel):
    """`GET`/`POST /ad-group-mapping-config` (4.4/7.3, Post-Roadmap Phase
    39 Session 3, ADR 0153) - the config-service export/import shape for
    ALL of this session's AD-group-mapping state in one bundle (simple
    mappings, composite rules, the default role), service-to-service-
    gated (`X-DMS-Principal`/`_require_service_user_management`) like
    `POST /realm-roles`, not the bearer-token admin CRUD endpoints above -
    see ADR 0153 "Rationale" for why. Import is idempotent (skips an
    exact existing mapping/rule instead of erroring, unconditionally
    overwrites `default_role_name`) and does NOT go through the four-eyes
    checks of the admin CRUD endpoints, the same pre-existing precedent as
    `POST /realm-roles`."""

    mappings: list[AdGroupRoleMappingIn] = []
    composite_rules: list[AdGroupCompositeRuleIn] = []
    default_role_name: str | None = None


class SuperuserStatus(BaseModel):
    active: bool
    expires_at: str | None = None
    # Since P6-S6 (4.8): permission-service must be able to check whether a
    # `POST /maintenance-mode/lift` caller is actually the active superuser.
    principal_id: str | None = None
