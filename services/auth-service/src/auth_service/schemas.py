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


class ThemePreference(BaseModel):
    theme: ThemeName = "auto"


class SuperuserStatus(BaseModel):
    active: bool
    expires_at: str | None = None
    # Seit P6-S6 (4.8): permission-service muss prüfen können, ob ein
    # `POST /maintenance-mode/lift`-Aufrufer tatsächlich der aktive Superuser ist.
    principal_id: str | None = None
