"""HTTP-Basic-Auth (5.2.9.1 "Basic Authentication for Non-Browser Clients")
gegen die bereits bestehende Authentifizierung geprüft - identisches Muster
wie `webdav_connector.domain_controller.DmsAuthDomainController` (P12-S1),
nur als FastAPI-Dependency statt wsgidav-`BaseDomainController`, da dieser
Connector (anders als der WSGI-basierte WebDAV-Connector) eine normale
FastAPI-App ist."""

import base64
import binascii
import logging

import httpx
from fastapi import Header, HTTPException, status

logger = logging.getLogger(__name__)


class BasicAuthClient:
    def __init__(self, auth_service_base_url: str) -> None:
        self._client = httpx.Client(base_url=auth_service_base_url, timeout=10.0)

    def close(self) -> None:
        self._client.close()

    def verify(self, username: str, password: str) -> bool:
        try:
            response = self._client.post(
                "/login", json={"username": username, "password": password}
            )
        except httpx.HTTPError:
            logger.warning("cmis_connector_auth_backend_unreachable")
            return False
        return response.status_code == 200


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Basic-Auth erforderlich",
        headers={"WWW-Authenticate": 'Basic realm="DMS"'},
    )


def parse_basic_auth(authorization: str | None = Header(default=None)) -> tuple[str, str]:
    """Eigenständige Dependency statt FastAPIs `HTTPBasic`-Security-Klasse,
    da diese bei fehlendem Header immer 403 statt des von CMIS 5.2.9.1
    vorgesehenen "401 + WWW-Authenticate"-Herausforderungsmusters wirft."""
    if authorization is None or not authorization.startswith("Basic "):
        raise _unauthorized()
    try:
        decoded = base64.b64decode(authorization[len("Basic ") :]).decode("utf-8")
        username, _, password = decoded.partition(":")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise _unauthorized() from exc
    if not username:
        raise _unauthorized()
    return username, password
