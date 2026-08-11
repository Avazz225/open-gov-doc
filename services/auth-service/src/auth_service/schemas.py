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


class SuperuserStatus(BaseModel):
    active: bool
    expires_at: str | None = None
    # Seit P6-S6 (4.8): permission-service muss prüfen können, ob ein
    # `POST /maintenance-mode/lift`-Aufrufer tatsächlich der aktive Superuser ist.
    principal_id: str | None = None
