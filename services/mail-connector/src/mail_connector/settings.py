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
    # Storage-Backends/Virenscan-Engines (3.3/3.6/10.3) - "pop3" (Default) und
    # seit P24-S3 "imap" implementiert (siehe backends/pop3_backend.py bzw.
    # backends/imap_backend.py). Microsoft Graph (Exchange/O365) ist über
    # dasselbe `MailboxBackend`-Interface vorbereitet, aber bewusst nicht
    # Teil dieser Session (siehe docs/services/mail-connector.md "Offene
    # Punkte").
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

    # IMAP-Gegenstück (P24-S3) - `mailpit` (Stand v1.30.6, siehe
    # `docker run axllent/mailpit --help`) bringt anders als beim POP3-Server
    # KEINEN eigenen IMAP-Server mit, es gibt deshalb (noch) keinen
    # Entwicklungsstandard-Selbst-Loopback für IMAP wie bei POP3 - Defaults
    # hier sind reine Platzhalter für einen echten externen IMAP-Server.
    # `imap_mailbox` ist IMAP-spezifisch (POP3 kennt keine benannten Ordner,
    # dort wird stets das gesamte - flache - Postfach abgeholt).
    imap_host: str = "localhost"
    imap_port: int = 993
    imap_username: str = "mailconnector"
    imap_password: str = "mailconnector"
    imap_use_tls: bool = True
    imap_mailbox: str = "INBOX"

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
