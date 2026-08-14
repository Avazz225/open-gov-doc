"""HTTP Basic Auth (5.2.9.1 "Basic Authentication for Non-Browser Clients")
checked against the already existing authentication - identical pattern
to `webdav_connector.domain_controller.DmsAuthDomainController` (P12-S1),
just as a FastAPI dependency instead of wsgidav's `BaseDomainController`, since
this connector (unlike the WSGI-based WebDAV connector) is a regular
FastAPI app."""

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
    """Standalone dependency instead of FastAPI's `HTTPBasic` security class,
    since that always raises 403 when the header is missing instead of the
    "401 + WWW-Authenticate" challenge pattern mandated by CMIS 5.2.9.1."""
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
