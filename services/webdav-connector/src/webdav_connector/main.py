import logging
from contextlib import asynccontextmanager

from asgiref.wsgi import WsgiToAsgi
from dms_common import configure_logging
from dms_connector_sdk import ConnectorDescriptor, DmsTreeClient
from dms_registry_client import maybe_start_registration
from fastapi import FastAPI
from wsgidav.wsgidav_app import WsgiDAVApp

from webdav_connector.dav_provider import DmsDavProvider
from webdav_connector.domain_controller import DmsAuthDomainController
from webdav_connector.license_client import LicenseStatusClient
from webdav_connector.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

# Konzept 3.3 wörtlich: "Capability-Beschreibung: lesen, schreiben, Metadaten,
# Locking, Versionierung" - alle fünf treffen auf diesen Referenz-Connector zu
# (Versionierung über document-service's bestehende Check-in-Historie, jedes
# `write_document()` auf ein existierendes Ziel checkt eine neue Version ein
# statt zu überschreiben).
_DESCRIPTOR = ConnectorDescriptor(
    protocol="webdav",
    capabilities=frozenset({"read", "write", "metadata", "locking", "versioning"}),
)

# Bewusst auf Modulebene statt in `lifespan` konstruiert: `DmsTreeClient`/
# `LicenseStatusClient` sind synchron (kein async-Connect-Schritt nötig, siehe
# deren Docstrings), und `app.mount()` nach Modul-Import statt erst beim
# ASGI-Lifespan-Start ist der etabliertere, unzweideutig sichere Zeitpunkt für
# WSGI-Unterapplikationen (kein Risiko einer Zustandsänderung an der Routing-
# Tabelle, während der Server ggf. schon dabei ist, sie zu lesen).
_tree = DmsTreeClient(
    document_service_base_url=settings.document_service_base_url,
    folder_service_base_url=settings.folder_service_base_url,
    root_folder_id=settings.webdav_root_folder_id,
)
_license_client = LicenseStatusClient(
    settings.registry_service_base_url or "",
    settings.service_name,
    settings.license_status_cache_ttl_seconds,
)

_wsgidav_config = {
    "provider_mapping": {"/": DmsDavProvider(_tree, _license_client)},
    "http_authenticator": {
        "domain_controller": DmsAuthDomainController,
        "accept_basic": True,
        "accept_digest": False,
        "default_to_digest": False,
    },
    # Eigener Konfigurationsschlüssel, von `DmsAuthDomainController.__init__`
    # gelesen (`BaseDomainController` bekommt das volle Config-Dict
    # durchgereicht) - kein offizieller wsgidav-Schlüssel, aber der
    # dokumentierte Weg, eigene Provider-/DC-Parameter durchzureichen.
    "dms_auth_service_base_url": settings.auth_service_base_url,
    # Office-Direktbearbeitung (Post-Roadmap-Feature): `DmsAuthDomainController`
    # löst WebDAV-Edit-Tokens direkt (Ost-West, kein Gateway) gegen
    # document-service auf, dasselbe Prinzip wie der `dms_auth_service_base_url`-
    # Schlüssel oben, nur für einen anderen Aufrufer.
    "dms_document_service_base_url": settings.document_service_base_url,
    # In-Prozess-Lock-Manager (RFC 4918 LOCK/UNLOCK, Opaque-Tokens, Timeouts,
    # If-Header-Prüfung) - ausreichend für eine einzelne Instanz, gleiches
    # Prinzip wie gateway-services `RateLimiter` (ADR 0005). Siehe
    # docs/services/webdav-connector.md für die bewusste Grenze ggü. einer
    # über die gesamte Bearbeitungsdauer gespiegelten document-service-Sperre.
    "lock_storage": True,
    # `WsgiToAsgi` mountet die App unter `settings.webdav_mount_path` (z. B.
    # "/webdav") innerhalb von FastAPI - wsgidav selbst weiss davon nichts und
    # wuerde sonst Hrefs relativ zu SEINER eigenen Wurzel ("/...") statt zur
    # tatsaechlich oeffentlichen URL ("/webdav/...") ausliefern. Ohne diesen
    # Schluessel interpretieren WebDAV-Clients (z. B. `webdav4`, das den
    # Ressourcennamen durch Abschneiden des Mount-Pfad-Praefixes vom Href
    # bestimmt) die Antwort falsch und liefern verstuemmelte Namen.
    "mount_path": settings.webdav_mount_path,
    "verbose": 1,
    "logging": {"enable": False},
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
        capabilities=_DESCRIPTOR.as_capability_list(),
    )

    logger.info("webdav_connector_startup_completed")
    yield

    if registration:
        await registration.stop()
    _tree.close()
    _license_client.close()


app = FastAPI(title=settings.service_name, lifespan=lifespan)
app.mount(settings.webdav_mount_path, WsgiToAsgi(WsgiDAVApp(_wsgidav_config)))


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}
