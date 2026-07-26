import httpx

from auth_service.settings import Settings


class InvalidCredentialsError(Exception):
    pass


def _token_endpoint(settings: Settings) -> str:
    base = f"{settings.keycloak_base_url}/realms/{settings.keycloak_realm}"
    return f"{base}/protocol/openid-connect/token"


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
