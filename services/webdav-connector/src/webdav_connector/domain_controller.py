import logging

import httpx
from wsgidav.dc.base_dc import BaseDomainController

logger = logging.getLogger(__name__)


class DmsAuthDomainController(BaseDomainController):
    """Maps WebDAV Basic Auth (RFC 4918/2617 - this is what Windows Explorer/
    macOS Finder/Word send when connecting to a network drive) onto the
    already existing authentication: checks username/password directly
    against `auth-service`'s `POST /login`, instead of introducing a second,
    connector-owned user store.

    Deliberately not reachable anonymously (`require_authentication()`
    always `True`): unlike the other 27 backend services (whose ports,
    per ADR 0005, are only directly open for developer convenience, while
    real usage always goes through the authenticating gateway), a WebDAV
    connector IS its own endpoint, addressed directly by external programs -
    without real authentication here, every document in the system would be
    readable/writable/deletable by anyone with network access."""

    def __init__(self, wsgidav_app, config) -> None:
        super().__init__(wsgidav_app, config)
        self._auth_client = httpx.Client(base_url=config["dms_auth_service_base_url"], timeout=10.0)
        # Office direct editing (post-roadmap feature): a dedicated,
        # synchronous client against document-service, direct (east-west,
        # no gateway) - the same call path that `DmsTreeClient` already
        # uses.
        self._document_client = httpx.Client(
            base_url=config["dms_document_service_base_url"], timeout=10.0
        )

    def get_domain_realm(self, path_info, environ):
        return "DMS"

    def require_authentication(self, realm, environ):
        return True

    def supports_http_digest_auth(self):
        # Basic Auth over HTTPS/TLS termination in the target environment is
        # an established, sufficiently secure standard for WebDAV clients
        # (Windows Explorer/Finder generally send Basic anyway) - Digest
        # would provide no real added benefit here, only more complexity.
        return False

    def _resolve_edit_token(self, token: str) -> str | None:
        """Office direct editing (post-roadmap feature): resolves a WebDAV
        edit token to the real identity (`principal_id`), which is then
        used as `environ["wsgidav.auth.user_name"]` - NOT the raw token
        itself, otherwise a later check-in would incorrectly use the token
        instead of the real identity as `created_by`/lock holder."""
        try:
            response = self._document_client.get(f"/internal/webdav-edit-tokens/{token}")
        except httpx.HTTPError:
            logger.warning("webdav_edit_token_resolution_backend_unreachable")
            return None
        if response.status_code != 200:
            return None
        return response.json()["principal_id"]

    def basic_auth_user(self, realm, user_name, password, environ):
        # Office direct editing (post-roadmap feature): the start URL embeds
        # an edit token as the Basic Auth "username" with an empty password
        # (`https://<token>:@<host>/webdav/by-id/...`) - a real WebDAV mount,
        # by contrast, always sends a real password. An empty password is
        # therefore a reliable distinguishing feature, without needing to
        # know/parse the token format itself.
        if not password:
            principal_id = self._resolve_edit_token(user_name)
            if principal_id is None:
                return False
            environ["wsgidav.auth.user_name"] = principal_id
            return True

        try:
            response = self._auth_client.post(
                "/login", json={"username": user_name, "password": password}
            )
        except httpx.HTTPError:
            logger.warning("webdav_auth_backend_unreachable")
            return False
        if response.status_code != 200:
            return False
        environ["wsgidav.auth.user_name"] = user_name
        return True
