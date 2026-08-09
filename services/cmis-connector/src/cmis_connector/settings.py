from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "cmis-connector"

    document_service_base_url: str = "http://localhost:8006"
    folder_service_base_url: str = "http://localhost:8008"
    auth_service_base_url: str = "http://localhost:8003"

    # Konzept-3.2a-Lizenzvermittlung (P9-S2-Muster), gleiches Verhalten wie
    # `webdav-connector`: Poll-Intervall des Lizenzstatus-Caches gegen
    # registry-service.
    license_status_cache_ttl_seconds: float = 30.0

    cmis_root_folder_id: str = "root"

    # CMIS kennt echte Multi-Repository-Server (getRepositories liefert eine
    # Map) - diese Referenzimplementierung bildet immer genau EINE
    # Installation dieser Software als genau EIN Repository ab, siehe
    # docs/services/cmis-connector.md "Bewusste Grenzen".
    cmis_repository_id: str = "default"
