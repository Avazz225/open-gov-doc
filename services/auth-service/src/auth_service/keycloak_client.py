from urllib.parse import urlencode

import httpx

from auth_service.settings import Settings


class InvalidCredentialsError(Exception):
    pass


def _realm_base(settings: Settings) -> str:
    return f"{settings.keycloak_base_url}/realms/{settings.keycloak_realm}"


def _public_realm_base(settings: Settings) -> str:
    """Wie `_realm_base`, aber für URLs, zu denen der BROWSER navigiert (nicht
    für Server-zu-Server-Aufrufe) - siehe `Settings.keycloak_public_base_url`-
    Docstring. Nur `_authorization_endpoint` braucht das, da nur `GET
    /oidc/authorize`s Antwort tatsächlich vom Client aufgerufen wird; Token-/
    Logout-Endpunkt werden immer serverseitig aus `auth-service` heraus
    angesprochen."""
    base = settings.keycloak_public_base_url or settings.keycloak_base_url
    return f"{base}/realms/{settings.keycloak_realm}"


def _token_endpoint(settings: Settings) -> str:
    return f"{_realm_base(settings)}/protocol/openid-connect/token"


def _authorization_endpoint(settings: Settings) -> str:
    return f"{_public_realm_base(settings)}/protocol/openid-connect/auth"


def _end_session_endpoint(settings: Settings) -> str:
    return f"{_realm_base(settings)}/protocol/openid-connect/logout"


async def login(settings: Settings, username: str, password: str) -> dict:
    """Password-Grant gegen Keycloak (4.4) - der Auth Service hält den
    Client-Secret, der Aufrufer sieht nur Benutzername/Passwort und bekommt
    fertige Tokens zurück.
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
    """SSO/automatischer Login (Post-Roadmap-Feature) - reine URL-Konstruktion,
    kein HTTP-Aufruf: der Browser navigiert selbst dorthin. Besitzt der
    Rechner ein gültiges Kerberos-Ticket UND ist die Kerberos-Erweiterung des
    Browser-Flows konfiguriert (siehe bootstrap.py), meldet Keycloaks
    SPNEGO-Mechanismus automatisch an, ohne dass diese Seite je sichtbar
    wird - andernfalls zeigt Keycloak selbst sein gehostetes Formular
    (Fallback)."""
    params = {
        "client_id": settings.keycloak_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid",
        "state": state,
    }
    return f"{_authorization_endpoint(settings)}?{urlencode(params)}"


async def exchange_code(settings: Settings, *, code: str, redirect_uri: str) -> dict:
    """Tauscht den von Keycloaks Redirect gelieferten `code` serverseitig
    gegen Tokens - da `dms-api` ein confidential Client ist (hält den
    `client_secret`), läuft dieser Austausch hier in `auth-service`, nie im
    Browser (kein PKCE nötig, siehe ADR)."""
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
    """Beendet die Sitzung wirklich auf Keycloak-Seite (Refresh-Token-
    Variante des OIDC-Logout-Endpunkts, kein `id_token_hint` nötig, da
    `TokenResponse` kein ID-Token führt) - ohne das würde ein SPNEGO-fähiger
    Browser sich nach einem lokalen "Abmelden" beim nächsten Besuch sofort
    wieder automatisch anmelden, da Keycloaks eigene SSO-Sitzung
    unangetastet bliebe."""
    async with httpx.AsyncClient() as client:
        await client.post(
            _end_session_endpoint(settings),
            data={
                "client_id": settings.keycloak_client_id,
                "client_secret": settings.keycloak_client_secret,
                "refresh_token": refresh_token,
            },
        )
