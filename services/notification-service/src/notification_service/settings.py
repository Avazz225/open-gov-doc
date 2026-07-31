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
    # auch `auth.superuser.activated`). Gezielt statt `workflow.>`/`auth.>`, da
    # nur ausgewählte Ereignisse Benachrichtigungs-Semantik haben. Künftige
    # Producer (Force-Unlock, Löschfrist-Vorankündigung, Lizenz-Ablauf, ...,
    # alle in Konzept an anderer Stelle erwähnt) tragen sich hier ein, sobald sie
    # tatsächlich angebunden werden - siehe "Offene Punkte" in
    # docs/services/notification-service.md.
    subjects: list[str] = ["workflow.task.escalated", "auth.superuser.activated"]

    # Empfänger der optionalen Sicherheitsbenachrichtigung bei Break-Glass-
    # Aktivierung (4.6, P6-S5).
    security_officer_email: str = "security@dms.local"
