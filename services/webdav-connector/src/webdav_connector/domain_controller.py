import logging

import httpx
from wsgidav.dc.base_dc import BaseDomainController

logger = logging.getLogger(__name__)


class DmsAuthDomainController(BaseDomainController):
    """Bildet WebDAV-Basic-Auth (RFC 4918/2617 - das ist es, was Windows-
    Explorer/macOS-Finder/Word beim Verbinden mit einem Netzlaufwerk senden)
    auf die bereits bestehende Authentifizierung ab: prüft Username/Passwort
    direkt gegen `auth-service`s `POST /login`, statt einen zweiten,
    connector-eigenen Nutzerspeicher einzuführen.

    Bewusst nicht anonym erreichbar (`require_authentication()` immer
    `True`): anders als die übrigen 27 Backend-Services (deren Ports laut
    ADR 0005 nur aus Entwickler-Komfort direkt offen sind, echte Nutzung aber
    stets über das authentifizierende Gateway läuft) IST ein WebDAV-Connector
    sein eigener, direkt von externen Programmen angesprochener Endpunkt -
    ohne echte Authentifizierung hier wäre jedes Dokument im System für jeden
    mit Netzwerkzugriff les-/schreib-/löschbar."""

    def __init__(self, wsgidav_app, config) -> None:
        super().__init__(wsgidav_app, config)
        self._auth_client = httpx.Client(base_url=config["dms_auth_service_base_url"], timeout=10.0)
        # Office-Direktbearbeitung (Post-Roadmap-Feature): eigener,
        # synchroner Client gegen document-service, direkt (Ost-West, kein
        # Gateway) - derselbe Aufrufweg, den `DmsTreeClient` bereits nutzt.
        self._document_client = httpx.Client(
            base_url=config["dms_document_service_base_url"], timeout=10.0
        )

    def get_domain_realm(self, path_info, environ):
        return "DMS"

    def require_authentication(self, realm, environ):
        return True

    def supports_http_digest_auth(self):
        # Basic-Auth über HTTPS/TLS-Termination in der Zielumgebung ist ein
        # etablierter, ausreichend sicherer Standard für WebDAV-Clients
        # (Windows-Explorer/Finder senden ohnehin i. d. R. Basic) - Digest
        # brächte hier keinen echten Zusatznutzen, nur mehr Komplexität.
        return False

    def _resolve_edit_token(self, token: str) -> str | None:
        """Office-Direktbearbeitung (Post-Roadmap-Feature): löst ein
        WebDAV-Edit-Token zur echten Identität auf (`principal_id`), die dann
        als `environ["wsgidav.auth.user_name"]` einsetzt wird - NICHT das
        rohe Token selbst, sonst würde ein späterer Check-in fälschlich das
        Token statt der echten Identität als `created_by`/Sperrinhaber
        verwenden."""
        try:
            response = self._document_client.get(f"/internal/webdav-edit-tokens/{token}")
        except httpx.HTTPError:
            logger.warning("webdav_edit_token_resolution_backend_unreachable")
            return None
        if response.status_code != 200:
            return None
        return response.json()["principal_id"]

    def basic_auth_user(self, realm, user_name, password, environ):
        # Office-Direktbearbeitung (Post-Roadmap-Feature): die Start-URL
        # bettet ein Edit-Token als Basic-Auth-"Benutzername" mit leerem
        # Passwort ein (`https://<token>:@<host>/webdav/by-id/...`) - ein
        # echter WebDAV-Mount sendet dagegen immer ein echtes Passwort. Leeres
        # Passwort ist damit ein sicheres Unterscheidungsmerkmal, ohne das
        # Token-Format selbst kennen/parsen zu müssen.
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
