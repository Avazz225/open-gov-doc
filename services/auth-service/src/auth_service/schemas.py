from pydantic import BaseModel


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
