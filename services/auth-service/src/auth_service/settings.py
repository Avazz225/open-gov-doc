from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "auth-service"

    keycloak_base_url: str = "http://localhost:8080"
    keycloak_realm: str = "dms"
    keycloak_client_id: str = "dms-api"
    keycloak_client_secret: str = "dms-api-dev-secret"
    keycloak_admin_username: str = "admin"
    keycloak_admin_password: str = "admin_dev_only"
