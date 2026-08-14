from dms_common import BaseServiceSettings

# Fixed special-folder IDs of folder-service (2.5/3.3, P15-S3) - the same
# hard-coded strings as `folder_service.settings.INBOX_FOLDER_ID`/
# `OUTBOX_FOLDER_ID`, duplicated here independently instead of imported (no
# cross-service code import in this project, see CONTRIBUTING.md "A
# service ... communicates with other services only via their API").
INBOX_FOLDER_ID = "inbox"
OUTBOX_FOLDER_ID = "outbox"


class Settings(BaseServiceSettings):
    service_name: str = "mail-connector"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    storage_service_base_url: str = "http://localhost:8005"
    virus_scan_service_base_url: str = "http://localhost:8010"
    document_service_base_url: str = "http://localhost:8006"
    case_service_base_url: str = "http://localhost:8016"
    # Post-Roadmap Phase 19 Session 11 - candidate pattern is derived from
    # the actually configured `kennzeichen_format` values (matching.py),
    # instead of being hard-coded.
    object_type_service_base_url: str = "http://localhost:8007"

    # Interchangeable retrieval protocol following the same plugin principle
    # as the storage backends/virus-scan engines (3.3/3.6/10.3) - "pop3"
    # (default) and, since P24-S3, "imap" implemented (see
    # backends/pop3_backend.py and backends/imap_backend.py respectively).
    # Microsoft Graph (Exchange/O365) is prepared for via the same
    # `MailboxBackend` interface, but deliberately not part of this session
    # (see docs/services/mail-connector.md "Open Points").
    inbound_protocol: str = "pop3"

    # Development standard: the already-existing `mailpit` container serves
    # as the self-loopback source (SMTP submission + its own POP3 server,
    # since mailpit v1.15) - no external mail server needed to genuinely
    # test the entire receiving path. Credentials must match the
    # `--pop3-auth-file` configured in `infra/docker-compose.yml`.
    pop3_host: str = "localhost"
    pop3_port: int = 1110
    pop3_username: str = "mailconnector"
    pop3_password: str = "mailconnector"
    pop3_use_tls: bool = False

    # IMAP counterpart (P24-S3) - unlike the POP3 server, `mailpit` (as of
    # v1.30.6, see `docker run axllent/mailpit --help`) does NOT ship its own
    # IMAP server, so there is (still) no development-standard self-loopback
    # for IMAP as there is for POP3 - the defaults here are pure placeholders
    # for a real external IMAP server. `imap_mailbox` is IMAP-specific (POP3
    # has no named folders, there the entire - flat - mailbox is always
    # retrieved).
    imap_host: str = "localhost"
    imap_port: int = 993
    imap_username: str = "mailconnector"
    imap_password: str = "mailconnector"
    imap_use_tls: bool = True
    imap_mailbox: str = "INBOX"

    # Outbound mail (2.5, P15-S3) - dedicated SMTP dispatch instead of
    # reusing notification-service (whose `POST /notifications` is limited to
    # already-known DMS users, see ADR 0053), same shape as
    # notification-service's `delivery.py`.
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_from_address: str = "poststelle@dms.local"
    smtp_use_tls: bool = False
    smtp_username: str | None = None
    smtp_password: str | None = None

    # How often the poll loop retrieves new messages (same idiom as
    # document-service's `retention_poll_interval_seconds`) - considerably
    # shorter than there, since mail room operation depends on timely
    # visibility of incoming mail, not on a coarse date comparison.
    poll_interval_seconds: float = 20.0

    # "A dedicated, narrowly scoped role may view/process the unreviewed
    # intake" (concept 2.5, verbatim) - deliberately NOT `dms-admin` as the
    # default (unlike the other role settings in this project): per the
    # concept, the mail room is a standalone operational role, not IT
    # administration.
    poststelle_role: str = "dms-poststelle"
