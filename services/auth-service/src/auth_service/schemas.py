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
    """SSO/automatischer Login (Post-Roadmap-Feature) - der Client navigiert
    selbst zu dieser URL, kein serverseitiger 307-Redirect (konsistent mit
    dem übrigen "Service liefert Daten, Client navigiert"-Stil dieses
    Projekts)."""

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
    """Minimale Antwort für `GET /users/lookup` (2.5, P14-S6) - bewusst nur
    `id`/`username`, siehe `admin_users.find_user_by_username`."""

    id: str
    username: str


class DirectoryEntryOut(BaseModel):
    """Verzeichnis-Eintrag (2.5/4.4, P15-S4) - bewusst ohne `enabled`
    (Freigabestatus eines Kontos ist eine administrative Angelegenheit,
    keine für das reine Auffinden einer Person nötige Information), sonst
    identisches Feldset wie `UserOut`."""

    id: str
    username: str
    email: str | None
    first_name: str | None
    last_name: str | None


class FederatedDirectoryEntryOut(DirectoryEntryOut):
    """Wie `DirectoryEntryOut`, ergänzt um die Herkunftsinstallation (2.5,
    "installationsübergreifende Kontaktsuche") - notwendig, da zwei
    Installationen unabhängige Nutzerpopulationen mit potenziell
    kollidierenden `id`s/Namen führen."""

    installation_id: str
    installation_display_name: str


class DirectorySearchRequest(BaseModel):
    """Payload einer eingehenden, signierten Verzeichnis-Suchanfrage einer
    Peer-Installation (`POST /users/directory/federated-search-inbound`)."""

    query: str


class DirectoryFederationStatusOut(BaseModel):
    enabled: bool
    peer_installation_count: int


class ThemePreference(BaseModel):
    theme: ThemeName = "auto"


class RealmRoleOut(BaseModel):
    name: str


class RealmRolesRequest(BaseModel):
    """Konfigurationspakete (14.1, P17-S1) können neue Keycloak-Realm-Rollen
    mitbringen (z. B. `dms-poststelle`, 2.5), für die es bislang keinen
    Importweg gab - siehe `bootstrap._ensure_dms_admin_role` für dasselbe
    Primitiv, hier auf beliebige, vom Paket vorgegebene Namen verallgemeinert."""

    names: list[str]


class AdGroupRoleMappingIn(BaseModel):
    """Payload für `POST /ad-group-mappings` (4.4, P24-S2) - bewusst nur
    einfache 1:1-Zuordnung, siehe `models.AdGroupRoleMapping`-Docstring."""

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
    # Seit P6-S6 (4.8): permission-service muss prüfen können, ob ein
    # `POST /maintenance-mode/lift`-Aufrufer tatsächlich der aktive Superuser ist.
    principal_id: str | None = None
