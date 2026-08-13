from dms_common import BaseServiceSettings

# Feste Sonderordner-IDs des folder-service (2.5/3.3, P15-S3) - dieselben
# hartcodierten Strings wie `folder_service.settings.INBOX_FOLDER_ID`/
# `OUTBOX_FOLDER_ID`, hier unabhängig dupliziert statt importiert (kein
# Cross-Service-Code-Import in diesem Projekt, siehe CONTRIBUTING.md "Ein
# Service ... kommuniziert mit anderen Services nur über deren API").
INBOX_FOLDER_ID = "inbox"
OUTBOX_FOLDER_ID = "outbox"


class Settings(BaseServiceSettings):
    service_name: str = "mail-connector"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    storage_service_base_url: str = "http://localhost:8005"
    virus_scan_service_base_url: str = "http://localhost:8010"
    document_service_base_url: str = "http://localhost:8006"
    case_service_base_url: str = "http://localhost:8016"
    # Post-Roadmap Phase 19 Session 11 - Kandidaten-Muster wird aus den
    # tatsächlich konfigurierten `kennzeichen_format`-Werten abgeleitet
    # (matching.py), statt fest kodiert zu sein.
    object_type_service_base_url: str = "http://localhost:8007"

    # Austauschbares Abhol-Protokoll nach demselben Plugin-Prinzip wie die
    # Storage-Backends/Virenscan-Engines (3.3/3.6/10.3) - aktuell nur "pop3"
    # implementiert (siehe backends/pop3_backend.py).
    inbound_protocol: str = "pop3"

    # Entwicklungsstandard: der bereits vorhandene `mailpit`-Container dient
    # als Selbst-Loopback-Quelle (SMTP-Einlieferung + eigener POP3-Server,
    # seit mailpit v1.15) - kein externer Mailserver nötig, um den gesamten
    # Empfangspfad echt zu testen. Zugangsdaten müssen zum in
    # `infra/docker-compose.yml` hinterlegten `--pop3-auth-file` passen.
    pop3_host: str = "localhost"
    pop3_port: int = 1110
    pop3_username: str = "mailconnector"
    pop3_password: str = "mailconnector"
    pop3_use_tls: bool = False

    # Postausgang (2.5, P15-S3) - eigener SMTP-Versand statt Wiederverwendung
    # von notification-service (dessen `POST /notifications` ist auf bereits
    # bekannte DMS-Nutzer beschränkt, siehe ADR 0053), gleicher Zuschnitt wie
    # notification-service's `delivery.py`.
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_from_address: str = "poststelle@dms.local"
    smtp_use_tls: bool = False
    smtp_username: str | None = None
    smtp_password: str | None = None

    # Wie oft der Poll-Loop neue Nachrichten abholt (gleiches Idiom wie
    # document-service's `retention_poll_interval_seconds`) - deutlich
    # kürzer als dort, da Poststelle-Betrieb auf zeitnahe Sichtbarkeit
    # eingehender Post angewiesen ist, nicht auf einen groben Datumsvergleich.
    poll_interval_seconds: float = 20.0

    # "Eine eigene, eng begrenzte Rolle darf den ungesichteten Zulauf sehen/
    # bearbeiten" (Konzept 2.5, wörtlich) - bewusst NICHT `dms-admin` als
    # Default (anders als die übrigen Rollen-Settings dieses Projekts): die
    # Poststelle ist laut Konzept eine eigenständige Betriebsrolle, keine
    # IT-Administration.
    poststelle_role: str = "dms-poststelle"
