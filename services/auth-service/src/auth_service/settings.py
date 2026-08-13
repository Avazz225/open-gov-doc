from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "auth-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    keycloak_base_url: str = "http://localhost:8080"
    # SSO/automatischer Login (Post-Roadmap-Feature): die vom BROWSER
    # erreichbare Keycloak-URL für den Redirect-Flow (`GET /oidc/authorize`
    # liefert eine `authorization_url`, zu der der Browser selbst navigiert).
    # Kann von `keycloak_base_url` abweichen - im Docker-Compose-Stack
    # spricht `auth-service` Keycloak intern über `http://keycloak:8080` an,
    # der Browser des Nutzers kennt diesen Hostnamen aber nicht und braucht
    # stattdessen den extern gemappten `http://localhost:8080`. Fällt auf
    # `keycloak_base_url` zurück, wenn nicht gesetzt (z. B. Tests/lokale
    # Entwicklung ohne Docker, wo beide identisch sind).
    keycloak_public_base_url: str | None = None
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

    # Föderierte Kontaktsuche (2.5/7.4, P15-S4) - eigene, von workflow-services
    # Federation-Hub-Teilnahme unabhängige Registrierung (siehe models.py).
    # Bewusst opt-in: bleibt `None`/`False`, bis eine Installation dies
    # ausdrücklich konfiguriert - "keine automatische Nebenwirkung der
    # bestehenden Föderationsfunktion" (Konzept 2.5, wörtlich).
    federation_hub_base_url: str | None = None
    federated_directory_enabled: bool = False
    installation_gateway_base_url: str = "http://gateway-service:8000"

    # SSO/automatischer Login (Post-Roadmap-Feature, Kerberos/SPNEGO über
    # Keycloak): der Browser-Redirect-Fluss (`standardFlowEnabled` +
    # Redirect-URIs) wird IMMER eingerichtet, unabhängig von Kerberos - das
    # ist der Teil, der auch ohne Kerberos-Konfiguration funktioniert
    # (Fallback auf Keycloaks eigenes gehostetes Login-Formular). Nur der
    # Kerberos-Teil selbst ist an diese drei Werte gebunden; fehlt einer,
    # wird `_ensure_kerberos` in bootstrap.py sauber übersprungen (kein
    # Domain-Controller/Keytab in einer frischen/sandbox-artigen Installation
    # nötig, um SSO grundsätzlich nutzbar zu machen).
    kerberos_enabled: bool = False
    kerberos_realm: str | None = None
    kerberos_server_principal: str | None = None
    kerberos_keytab_path: str | None = None

    # Erlaubte Origins für `redirect_uri` bei `GET /oidc/authorize` (Open-
    # Redirect-Absicherung) - gleiches Listen-Setting-Muster wie gateway-
    # services `cors_allowed_origins`. Bewusst nur user-ui in dieser Session
    # (siehe ADR) - weitere Frontends folgen bei Bedarf demselben Muster.
    sso_redirect_uri_allowed_origins: list[str] = ["http://localhost:3000"]
