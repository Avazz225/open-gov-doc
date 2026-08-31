from typing import Literal

from dms_common import BaseServiceSettings
from pydantic import BaseModel, model_validator

# Fixed special-folder IDs of folder-service (2.5/3.3, P15-S3) - the same
# hard-coded strings as `folder_service.settings.INBOX_FOLDER_ID`/
# `OUTBOX_FOLDER_ID`, duplicated here independently instead of imported (no
# cross-service code import in this project, see CONTRIBUTING.md "A
# service ... communicates with other services only via their API").
INBOX_FOLDER_ID = "inbox"
OUTBOX_FOLDER_ID = "outbox"


class MailboxConfig(BaseModel):
    """A single named inbox (14.2, Post-Roadmap Phase 31 Session 12a) - the
    multi-inbox counterpart to `storage-service`'s `BackendTargetConfig`
    (same env-var-JSON-list pattern, ADR 0004/0017): any number of mailboxes,
    each with its own retrieval protocol/credentials, configured via
    `DMS_MAILBOXES` (a JSON list) rather than a DB-backed CRUD - credentials
    deliberately stay env-var/restart-only, the same choice already made for
    storage-service/signature-service connector credentials ([ADR
    0091](../adr/0091-connector-operational-config-live-editable.md)):
    making them live-editable would need new encryption/masking
    infrastructure, out of scope here too."""

    id: str
    name: str
    kind: Literal["central", "departmental"] = "central"
    # Org-hierarchy foundation (P31-S9) reused, not a new concept - the same
    # choice P31-S10 already made for org-hierarchy grants' "org_unit"
    # target. A departmental mailbox's owner is a permission-service
    # `Group.id`, by convention (no FK enforcement across service
    # boundaries, same pattern as `folder_id`/`object_type_id` elsewhere).
    owning_group_id: str | None = None

    # Interchangeable retrieval protocol following the same plugin principle
    # as the storage backends/virus-scan engines (3.3/3.6/10.3).
    inbound_protocol: Literal["pop3", "imap"] = "pop3"
    pop3_host: str | None = None
    pop3_port: int = 995
    pop3_username: str | None = None
    pop3_password: str | None = None
    pop3_use_tls: bool = True
    imap_host: str | None = None
    imap_port: int = 993
    imap_username: str | None = None
    imap_password: str | None = None
    imap_use_tls: bool = True
    imap_mailbox: str = "INBOX"

    @model_validator(mode="after")
    def _check_required_fields(self) -> "MailboxConfig":
        if self.kind == "departmental" and not self.owning_group_id:
            raise ValueError(
                f"Postfach {self.id!r}: owning_group_id ist fuer kind=departmental erforderlich"
            )
        if self.kind == "central" and self.owning_group_id:
            raise ValueError(
                f"Postfach {self.id!r}: owning_group_id ist nur fuer kind=departmental zulaessig"
            )
        if self.inbound_protocol == "pop3" and not all(
            [self.pop3_host, self.pop3_username, self.pop3_password]
        ):
            raise ValueError(
                f"Postfach {self.id!r}: pop3_host/pop3_username/pop3_password sind fuer "
                "inbound_protocol=pop3 erforderlich"
            )
        if self.inbound_protocol == "imap" and not all(
            [self.imap_host, self.imap_username, self.imap_password]
        ):
            raise ValueError(
                f"Postfach {self.id!r}: imap_host/imap_username/imap_password sind fuer "
                "inbound_protocol=imap erforderlich"
            )
        return self


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
    monitoring_service_base_url: str = "http://localhost:8026"

    # Multi-inbox model (14.2, Post-Roadmap Phase 31 Session 12a) - replaces
    # the previous single, service-wide `inbound_protocol`/`pop3_*`/`imap_*`
    # settings block with a list of any number of named mailboxes. The
    # single default entry below reproduces the previous single-mailbox
    # behavior exactly (same `mailpit` self-loopback dev target, same
    # credentials) under the fixed id `"central"` - existing `docker-
    # compose.yml` deployments are migrated to `DMS_MAILBOXES` rather than
    # kept on a legacy fallback path (see docs/services/mail-connector.md).
    mailboxes: list[MailboxConfig] = [
        MailboxConfig(
            id="central",
            name="Zentrale Poststelle",
            kind="central",
            inbound_protocol="pop3",
            pop3_host="localhost",
            pop3_port=1110,
            pop3_username="mailconnector",
            pop3_password="mailconnector",
            pop3_use_tls=False,
        )
    ]

    @model_validator(mode="after")
    def _check_unique_mailbox_ids(self) -> "Settings":
        ids = [mb.id for mb in self.mailboxes]
        if len(ids) != len(set(ids)):
            raise ValueError("mailboxes: doppelte id-Werte sind nicht zulaessig")
        return self

    # Outbound mail (2.5, P15-S3) - dedicated SMTP dispatch instead of
    # reusing notification-service (whose `POST /notifications` is limited to
    # already-known DMS users, see ADR 0053), same shape as
    # notification-service's `delivery.py`. Deliberately still a single,
    # service-wide config (Post-Roadmap Phase 31 Session 12a scopes the
    # multi-inbox model to INBOUND mail only, per the plan's own wording -
    # see ADR for this session).
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_from_address: str = "poststelle@dms.local"
    smtp_use_tls: bool = False
    smtp_username: str | None = None
    smtp_password: str | None = None

    # How often the poll loop retrieves new messages (same idiom as
    # document-service's `retention_poll_interval_seconds`) - considerably
    # shorter than there, since mail room operation depends on timely
    # visibility of incoming mail, not on a coarse date comparison. Since
    # Post-Roadmap Phase 31 Session 12a, one loop tick polls every
    # configured mailbox in turn (not one interval per mailbox) - simpler
    # than N concurrent poll tasks, and mail room operation does not need
    # per-mailbox cadence control.
    poll_interval_seconds: float = 20.0

    # "A dedicated, narrowly scoped role may view/process the unreviewed
    # intake" (concept 2.5, verbatim) - deliberately NOT `dms-admin` as the
    # default (unlike the other role settings in this project): per the
    # concept, the mail room is a standalone operational role, not IT
    # administration. Still a single, global role for Post-Roadmap Phase 31
    # Session 12a (unchanged) - per-mailbox/per-department visibility
    # narrowing is a deliberately deferred, separate RBAC design question,
    # see this session's ADR "Open Points".
    poststelle_role: str = "dms-poststelle"
