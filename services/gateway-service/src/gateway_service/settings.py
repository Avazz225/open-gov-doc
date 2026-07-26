from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "gateway-service"

    registry_service_base_url: str = "http://localhost:8001"
    instance_cache_ttl_seconds: float = 5.0

    keycloak_base_url: str = "http://localhost:8080"
    keycloak_realm: str = "dms"
    keycloak_client_id: str = "dms-api"

    # "{service_type}:{path}" (Pfad ohne führenden Slash, exakter Match) - Routen,
    # die ohne Bearer-Token erreichbar sein müssen, allen voran Login/Refresh
    # selbst (man braucht ja erst einen Token, um einen Token zu bekommen).
    public_routes: list[str] = ["auth-service:login", "auth-service:refresh"]

    rate_limit_max_requests: int = 120
    rate_limit_window_seconds: float = 60.0

    upstream_timeout_seconds: float = 30.0
