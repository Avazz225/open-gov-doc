from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "gateway-service"

    registry_service_base_url: str = "http://localhost:8001"
    instance_cache_ttl_seconds: float = 5.0

    keycloak_base_url: str = "http://localhost:8080"
    keycloak_realm: str = "dms"
    keycloak_client_id: str = "dms-api"

    # Browser-Frontends (user-ui/admin-ui, Konzept 8) laufen auf einer anderen
    # Origin als das Gateway - ohne CORS-Freigabe scheitert bereits der
    # Preflight-OPTIONS-Request mit 405, bevor der eigentliche Request
    # überhaupt gesendet wird (curl-basierte Tests decken das nicht ab, da
    # curl keinen Origin-Header setzt und damit keinen Preflight auslöst).
    cors_allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    # "{service_type}:{path}" (Pfad ohne führenden Slash, exakter Match) - Routen,
    # die ohne Bearer-Token erreichbar sein müssen, allen voran Login/Refresh
    # selbst (man braucht ja erst einen Token, um einen Token zu bekommen).
    public_routes: list[str] = ["auth-service:login", "auth-service:refresh"]

    rate_limit_max_requests: int = 120
    rate_limit_window_seconds: float = 60.0

    upstream_timeout_seconds: float = 30.0
