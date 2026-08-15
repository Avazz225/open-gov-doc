from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "webdav-connector"

    document_service_base_url: str = "http://localhost:8006"
    folder_service_base_url: str = "http://localhost:8008"
    auth_service_base_url: str = "http://localhost:8003"
    monitoring_service_base_url: str = "http://localhost:8026"

    # Concept 3.2a license mediation (P9-S2 pattern): poll interval of the
    # license status cache against registry-service.
    license_status_cache_ttl_seconds: float = 30.0

    webdav_root_folder_id: str = "root"

    # WebDAV locking (4.2) runs in duplicate: wsgidav manages the actual
    # opaque lock tokens/timeouts/If-header checking itself (RFC 4918, see
    # docs/services/webdav-connector.md), and additionally the connector
    # holds the real document-service lock for the duration of each write
    # operation (PUT/DELETE/MOVE) - default timeout for that, in case the
    # operation takes unusually long.
    document_lock_timeout_seconds: float = 60.0

    # Port on which wsgidav runs internally, before `WsgiToAsgi` mounts it
    # under `/webdav` in the FastAPI app - not a network listener of its
    # own.
    webdav_mount_path: str = "/webdav"
