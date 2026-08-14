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

# Concept 3.3 verbatim: "Capability description: read, write, metadata,
# locking, versioning" - all five apply to this reference connector
# (versioning via document-service's existing check-in history, every
# `write_document()` against an existing target checks in a new version
# instead of overwriting).
_DESCRIPTOR = ConnectorDescriptor(
    protocol="webdav",
    capabilities=frozenset({"read", "write", "metadata", "locking", "versioning"}),
)

# Deliberately constructed at module level instead of in `lifespan`:
# `DmsTreeClient`/`LicenseStatusClient` are synchronous (no async connect
# step needed, see their docstrings), and calling `app.mount()` after module
# import rather than only at ASGI lifespan startup is the more established,
# unambiguously safe point in time for WSGI sub-applications (no risk of a
# state change to the routing table while the server may already be reading
# it).
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
    # Dedicated configuration key, read by `DmsAuthDomainController.__init__`
    # (`BaseDomainController` receives the full config dict passed through) -
    # not an official wsgidav key, but the documented way to pass through
    # custom provider/DC parameters.
    "dms_auth_service_base_url": settings.auth_service_base_url,
    # Office direct editing (post-roadmap feature): `DmsAuthDomainController`
    # resolves WebDAV edit tokens directly (east-west, no gateway) against
    # document-service, the same principle as the `dms_auth_service_base_url`
    # key above, just for a different caller.
    "dms_document_service_base_url": settings.document_service_base_url,
    # In-process lock manager (RFC 4918 LOCK/UNLOCK, opaque tokens, timeouts,
    # If-header checking) - sufficient for a single instance, same principle
    # as gateway-service's `RateLimiter` (ADR 0005). See
    # docs/services/webdav-connector.md for the deliberate boundary versus a
    # document-service lock mirrored over the entire editing duration.
    "lock_storage": True,
    # `WsgiToAsgi` mounts the app under `settings.webdav_mount_path` (e.g.
    # "/webdav") within FastAPI - wsgidav itself has no knowledge of this and
    # would otherwise serve hrefs relative to ITS OWN root ("/...") instead
    # of the actually public URL ("/webdav/..."). Without this key, WebDAV
    # clients (e.g. `webdav4`, which determines the resource name by
    # stripping the mount path prefix from the href) misinterpret the
    # response and produce mangled names.
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
