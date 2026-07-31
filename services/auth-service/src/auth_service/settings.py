from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "auth-service"

    keycloak_base_url: str = "http://localhost:8080"
    keycloak_realm: str = "dms"
    keycloak_client_id: str = "dms-api"
    keycloak_client_secret: str = "dms-api-dev-secret"
    keycloak_admin_username: str = "admin"
    keycloak_admin_password: str = "admin_dev_only"

    permission_service_base_url: str = "http://localhost:8004"
    # Erster Konsument dieses Service (P6-S5, Superuser Break-Glass, 4.6).
    subjects: list[str] = ["permission.approval.approved"]

    # Break-Glass-Aktivierung (4.6): bewusste Vereinfachung eines einzigen
    # absoluten Ablauf-Zeitstempels statt getrennter Gesamtdauer-/Inaktivitäts-
    # Timer (siehe ADR 0023) - durchgesetzt über denselben Poll-Loop-Ansatz wie
    # die SLA-Zeitüberwachung in workflow-service (ADR 0020).
    superuser_activation_minutes: int = 30
    superuser_poll_interval_seconds: float = 30.0
