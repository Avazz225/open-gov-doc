from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "notification-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Dev-Default zeigt auf den mailpit-Container (infra/docker-compose.yml) - kein
    # Auth nötig. Für echten SMTP-Betrieb smtp_username/smtp_password setzen.
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_from_address: str = "noreply@dms.local"
    smtp_use_tls: bool = False
    smtp_username: str | None = None
    smtp_password: str | None = None

    # Welche Subjects der Notification Service konsumiert (P6-S2, seit P6-S5
    # auch `auth.superuser.activated`, seit P6-S6 zusätzlich
    # `permission.maintenance_mode.activated`). Gezielt statt `workflow.>`/
    # `auth.>`/`permission.>`, da nur ausgewählte Ereignisse Benachrichtigungs-
    # Semantik haben. Künftige Producer (Force-Unlock, Löschfrist-
    # Vorankündigung, Lizenz-Ablauf, ..., alle in Konzept an anderer Stelle
    # erwähnt) tragen sich hier ein, sobald sie tatsächlich angebunden werden -
    # siehe "Offene Punkte" in docs/services/notification-service.md.
    subjects: list[str] = [
        "workflow.task.escalated",
        "auth.superuser.activated",
        "permission.maintenance_mode.activated",
        # Federation Hub (7.4, P6-S9): Benachrichtigung der Zielinstallation bei
        # einer eingehenden föderierten Übergabe, siehe consumer.py.
        "workflow.federation.inbound_received",
    ]

    # Empfänger der optionalen Sicherheitsbenachrichtigung bei Break-Glass-
    # Aktivierung (4.6, P6-S5) und Not-Shutdown (4.8, P6-S6).
    security_officer_email: str = "security@dms.local"

    # Retrofit P6-S6 (Aufrufautorisierung): Empfänger-Existenzprüfung für
    # `POST /notifications` gegen echte auth-service-Konten. `GET /users` ist
    # seit P6-S5 selbst gegated (Capability `admin.user_management`) - dieser
    # Service authentifiziert sich dafür mit dem technischen `users-admin`-
    # Konto aus P6-S5 (Domäne "Nutzer-/Rechteverwaltung"), genau der
    # vorgesehene Anwendungsfall für automatisierte interne Aufrufe.
    auth_service_base_url: str = "http://localhost:8003"
    auth_service_admin_username: str = "users-admin"
    auth_service_admin_password: str = "users-admin"
