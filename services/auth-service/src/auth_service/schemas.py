from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ThemeName = Literal["light", "dark", "high-contrast", "auto"]


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


class SuperuserStatus(BaseModel):
    active: bool
    expires_at: str | None = None
    # Since P6-S6 (4.8): permission-service must be able to check whether a
    # `POST /maintenance-mode/lift` caller is actually the active superuser.
    principal_id: str | None = None
