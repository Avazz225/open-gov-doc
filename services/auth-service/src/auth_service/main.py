from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_auth_client import TokenValidator, make_current_user_dependency
from dms_common import configure_logging
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, HTTPException, status

from auth_service import admin_users, keycloak_client
from auth_service.admin_users import UserAlreadyExistsError, UserNotFoundError, build_admin_client
from auth_service.bootstrap import ensure_realm_and_client
from auth_service.keycloak_client import InvalidCredentialsError
from auth_service.schemas import (
    LoginRequest,
    RefreshRequest,
    ThemePreference,
    TokenResponse,
    UserCreate,
    UserOut,
)
from auth_service.settings import Settings

settings = Settings()
configure_logging(settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    ensure_realm_and_client(settings)
    app.state.keycloak_admin = build_admin_client(settings)

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    yield

    if registration:
        await registration.stop()


app = FastAPI(title=settings.service_name, lifespan=lifespan)

_issuer = f"{settings.keycloak_base_url}/realms/{settings.keycloak_realm}"
_validator = TokenValidator(
    issuer=_issuer,
    audience=settings.keycloak_client_id,
    jwks_url=f"{_issuer}/protocol/openid-connect/certs",
)
get_current_user = make_current_user_dependency(_validator)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest) -> TokenResponse:
    try:
        tokens = await keycloak_client.login(settings, payload.username, payload.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return TokenResponse(**tokens)


@app.post("/refresh", response_model=TokenResponse)
async def refresh_token(payload: RefreshRequest) -> TokenResponse:
    try:
        tokens = await keycloak_client.refresh(settings, payload.refresh_token)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return TokenResponse(**tokens)


@app.get("/me")
async def me(user: dict = Depends(get_current_user)) -> dict:
    """Normalisierte Identität aus dem Token (4.4) - die Übersetzung in
    interne DMS-Rollen übernimmt der Permission Service (4.1, P2-S2), nicht
    der Auth Service selbst.
    """
    return {
        "sub": user.get("sub"),
        "username": user.get("preferred_username"),
        "email": user.get("email"),
        "realm_roles": user.get("realm_access", {}).get("roles", []),
    }


@app.get("/me/preferences", response_model=ThemePreference)
def get_my_preferences(user: dict = Depends(get_current_user)) -> ThemePreference:
    """Cross-UI-Theming (8, P4-S6) - Präferenz hängt am Nutzerkonto (Keycloak-
    Attribut), nicht an einer einzelnen Installation/einem einzelnen Browser,
    damit sie geräteübergreifend wirkt (Nutzer-Feedback nach P4-S5)."""
    theme = admin_users.get_theme_preference(app.state.keycloak_admin, user["sub"])
    return ThemePreference(theme=theme)


@app.put("/me/preferences", response_model=ThemePreference)
def update_my_preferences(
    payload: ThemePreference, user: dict = Depends(get_current_user)
) -> ThemePreference:
    admin_users.set_theme_preference(app.state.keycloak_admin, user["sub"], payload.theme)
    return payload


@app.get("/users", response_model=list[UserOut])
def list_users() -> list[dict]:
    """Nutzerverwaltung für die Admin-UI (8, seit P4-S3) - liest direkt aus
    Keycloak, keine eigene Nutzertabelle (siehe README: Konten sind bereits
    vollständig durch Keycloak abgedeckt)."""
    return admin_users.list_users(app.state.keycloak_admin)


@app.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate) -> dict:
    try:
        return admin_users.create_user(
            app.state.keycloak_admin,
            username=payload.username,
            email=payload.email,
            password=payload.password,
            first_name=payload.first_name,
            last_name=payload.last_name,
        )
    except UserAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@app.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: str) -> None:
    try:
        admin_users.delete_user(app.state.keycloak_admin, user_id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
