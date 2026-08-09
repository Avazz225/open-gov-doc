from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "webdav-connector"

    document_service_base_url: str = "http://localhost:8006"
    folder_service_base_url: str = "http://localhost:8008"
    auth_service_base_url: str = "http://localhost:8003"

    # Konzept-3.2a-Lizenzvermittlung (P9-S2-Muster): Poll-Intervall des
    # Lizenzstatus-Caches gegen registry-service.
    license_status_cache_ttl_seconds: float = 30.0

    webdav_root_folder_id: str = "root"

    # WebDAV-Locking (4.2) läuft doppelt: wsgidav verwaltet die eigentlichen
    # Opaque-Lock-Tokens/Timeouts/If-Header-Prüfung selbst (RFC 4918, siehe
    # docs/services/webdav-connector.md), zusätzlich hält der Connector für
    # die Dauer jeder Schreiboperation (PUT/DELETE/MOVE) die reale
    # document-service-Sperre - Standard-Timeout dafür, falls die Operation
    # ungewöhnlich lange dauert.
    document_lock_timeout_seconds: float = 60.0

    # Port, unter dem wsgidav intern läuft, bevor `WsgiToAsgi` es unter
    # `/webdav` in die FastAPI-App mountet - kein eigener Netzwerk-Listener.
    webdav_mount_path: str = "/webdav"
