from urllib.parse import urlencode

import httpx

from auth_service.settings import Settings


class InvalidCredentialsError(Exception):
    pass


def _realm_base(settings: Settings) -> str:
    return f"{settings.keycloak_base_url}/realms/{settings.keycloak_realm}"


def _public_realm_base(settings: Settings) -> str:
    """Like `_realm_base`, but for URLs the BROWSER navigates to (not for
    server-to-server calls) - see `Settings.keycloak_public_base_url`
    docstring. Only `_authorization_endpoint` needs this, since only `GET
    /oidc/authorize`'s response is actually invoked by the client; the
    token/logout endpoints are always addressed server-side from within
    `auth-service`."""
    base = settings.keycloak_public_base_url or settings.keycloak_base_url
    return f"{base}/realms/{settings.keycloak_realm}"


def _token_endpoint(settings: Settings) -> str:
    return f"{_realm_base(settings)}/protocol/openid-connect/token"


def _authorization_endpoint(settings: Settings) -> str:
    return f"{_public_realm_base(settings)}/protocol/openid-connect/auth"


def _end_session_endpoint(settings: Settings) -> str:
    return f"{_realm_base(settings)}/protocol/openid-connect/logout"


async def login(settings: Settings, username: str, password: str) -> dict:
    """Password grant against Keycloak (4.4) - the Auth Service holds the
    client secret, the caller only sees username/password and gets back
    finished tokens.
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            _token_endpoint(settings),
            data={
                "grant_type": "password",
                "client_id": settings.keycloak_client_id,
                "client_secret": settings.keycloak_client_secret,
                "username": username,
                "password": password,
            },
        )
    if response.status_code != 200:
        raise InvalidCredentialsError(response.json().get("error_description", "login failed"))
    return response.json()


async def refresh(settings: Settings, refresh_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            _token_endpoint(settings),
            data={
                "grant_type": "refresh_token",
                "client_id": settings.keycloak_client_id,
                "client_secret": settings.keycloak_client_secret,
                "refresh_token": refresh_token,
            },
        )
    if response.status_code != 200:
        raise InvalidCredentialsError(response.json().get("error_description", "refresh failed"))
    return response.json()


def authorization_url(settings: Settings, *, redirect_uri: str, state: str) -> str:
    """SSO/automatic login (post-roadmap feature) - pure URL construction,
    no HTTP call: the browser navigates there itself. If the machine has a
    valid Kerberos ticket AND the Kerberos extension of the browser flow is
    configured (see bootstrap.py), Keycloak's SPNEGO mechanism logs in
    automatically without this page ever becoming visible - otherwise
    Keycloak itself shows its hosted form (fallback)."""
    params = {
        "client_id": settings.keycloak_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid",
        "state": state,
    }
    return f"{_authorization_endpoint(settings)}?{urlencode(params)}"


async def exchange_code(settings: Settings, *, code: str, redirect_uri: str) -> dict:
    """Exchanges the `code` delivered by Keycloak's redirect for tokens
    server-side - since `dms-api` is a confidential client (holds the
    `client_secret`), this exchange happens here in `auth-service`, never in
    the browser (no PKCE needed, see ADR)."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            _token_endpoint(settings),
            data={
                "grant_type": "authorization_code",
                "client_id": settings.keycloak_client_id,
                "client_secret": settings.keycloak_client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
    if response.status_code != 200:
        raise InvalidCredentialsError(
            response.json().get("error_description", "code exchange failed")
        )
    return response.json()


async def end_session(settings: Settings, refresh_token: str) -> None:
    """Actually ends the session on Keycloak's side (refresh-token variant
    of the OIDC logout endpoint, no `id_token_hint` needed since
    `TokenResponse` carries no ID token) - without this, a SPNEGO-capable
    browser would immediately log back in automatically on its next visit
    after a local "logout", since Keycloak's own SSO session would remain
    untouched."""
    async with httpx.AsyncClient() as client:
        await client.post(
            _end_session_endpoint(settings),
            data={
                "client_id": settings.keycloak_client_id,
                "client_secret": settings.keycloak_client_secret,
                "refresh_token": refresh_token,
            },
        )
