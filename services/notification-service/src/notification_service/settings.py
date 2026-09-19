from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "notification-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Dev default points to the mailpit container (infra/docker-compose.yml) - no
    # auth required. For real SMTP operation, set smtp_username/smtp_password.
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_from_address: str = "noreply@dms.local"
    smtp_use_tls: bool = False
    smtp_username: str | None = None
    smtp_password: str | None = None

    # Retry/backoff (Post-Roadmap Phase 20 Session 3, ADR 0079) - same numeric
    # value as archival-service's `max_archival_attempts`/storage-service's
    # `max_replication_attempts`. Once exhausted, the notification switches to
    # `failed_permanent`.
    max_notification_attempts: int = 5

    # Poll interval of the new `_notification_retry_poll_loop` (main.py) -
    # significantly shorter than e.g. archival-service's
    # `archival_poll_interval_seconds` (hours), since retrying an email/webhook
    # delivery typically makes sense within seconds to minutes, not hours.
    notification_retry_poll_interval_seconds: float = 60.0

    # Which subjects the notification service consumes (P6-S2, since P6-S5 also
    # `auth.superuser.activated`, since P6-S6 additionally
    # `permission.maintenance_mode.activated`). Targeted instead of `workflow.>`/
    # `auth.>`/`permission.>`, since only selected events carry notification
    # semantics. Future producers (force unlock, deletion-deadline advance
    # notice, license expiry, ..., all mentioned elsewhere in the concept)
    # register here once actually wired up - see "Open Points" in
    # docs/services/notification-service.md.
    subjects: list[str] = [
        "workflow.task.escalated",
        "auth.superuser.activated",
        "permission.maintenance_mode.activated",
        # Federation Hub (7.4, P6-S9): notification to the target installation
        # for an incoming federated handover, see consumer.py.
        "workflow.federation.inbound_received",
        # Deletion reminder (5.2a, P7-S1) - a dedicated "document" stream
        # subject not previously consumed by this service, see consumer.py.
        "document.deletion.reminder",
        # Locked-document reminder (4.2, post-roadmap phase 30 session 4,
        # ADR 0111) - second subject on the "document" stream, hence its own
        # durable name (see `_SHARED_STREAM_DURABLE_OVERRIDES`), same
        # reasoning as `workflow.federation.inbound_received` above.
        "document.lock.reminder",
        # Deletion reminder for folders (5.2a, P7-S1b) - a dedicated "folder"
        # stream subject not previously consumed by this service. The first
        # subject of this service on the "folder" stream, so no second durable
        # name is needed (unlike `workflow.federation.inbound_received`).
        "folder.deletion.reminder",
        # License status changes (9.2, since P9-S1) - the three concrete,
        # explicitly named edge events from `license-service`'s poll loop,
        # see consumer.py.
        "license.limit_exceeded",
        "license.expiring_soon",
        "license.invalid",
        # Claim-abandonment notice (14.2/8, Post-Roadmap Phase 35 Session 3,
        # ADR 0145) - third subject on the "workflow" stream, own durable
        # name (see `_SHARED_STREAM_DURABLE_OVERRIDES`), same reasoning as
        # `workflow.federation.inbound_received` above.
        "workflow.task_claim.abandoned",
        # Virus-hit notification to the uploader (10.3, Post-Roadmap Phase
        # 44 Session 4) - `virus-scan-service` already published this
        # unconditionally (both "clean" and "infected") since it was
        # built; this is the first-ever consumer. First (and only)
        # subject of this service on the "virus_scan" stream, so no
        # `_SHARED_STREAM_DURABLE_OVERRIDES` entry is needed.
        "virus_scan.completed",
    ]

    # Recipient of the optional security notification on break-glass
    # activation (4.6, P6-S5) and emergency shutdown (4.8, P6-S6).
    security_officer_email: str = "security@dms.local"

    # Recipient of the license status notifications (9.2, since P9-S1) - hard
    # coded, same rationale as `security_officer_email` (no recipient
    # resolution mechanism needed).
    license_admin_email: str = "license-admin@dms.local"

    # Retrofit P6-S6 (call authorization): recipient existence check for
    # `POST /notifications` against real auth-service accounts, via the
    # service-to-service `GET /users/service-directory` (capability
    # `service.user_lookup`, `X-DMS-Principal: notification-service`, see
    # `AuthServiceClient`). Since Phase 50 Session 2: no longer a `users-admin`
    # login (that was a real excess-privilege exposure - see
    # `docs/services/notification-service.md` "Open Points"), so no
    # credential settings needed any more either.
    auth_service_base_url: str = "http://localhost:8003"

    monitoring_service_base_url: str = "http://localhost:8026"

    # RBAC (Post-Roadmap Phase 38 Session 2) - `POST /notifications`
    # previously checked recipient existence but not caller permission at
    # all: any authenticated principal could trigger a notification to any
    # known user (a spam/abuse vector), see
    # docs/services/notification-service.md "Open Points".
    permission_service_base_url: str = "http://localhost:8004"

    # Rate limiting (Post-Roadmap Phase 38 Session 2, same session as the
    # RBAC fix above) - defense in depth against a misconfigured/fast-
    # cycling caller repeatedly notifying the same recipient, even after
    # the RBAC fix restricts callers to `notification.write` holders.
    # In-process (not Redis-backed like gateway-service's, ADR 0097) since
    # this service runs as a single instance, same rationale as
    # gateway-service's own pre-ADR-0097 design (ADR 0005).
    notification_rate_limit_max_per_recipient: int = 20
    notification_rate_limit_window_seconds: float = 60.0

    # Direct links (post-roadmap feature, Phase 27, ADR 0105): base URLs of
    # the frontend apps reachable from the BROWSER, so notification emails
    # can embed a clickable link straight to a resource. Same rationale as
    # auth-service's `keycloak_public_base_url` - this service only ever
    # talks to other services over the internal Compose network, which
    # knows nothing about the host-mapped ports a user's browser needs.
    # `None` until an installation explicitly sets one; unresolvable
    # link-building is skipped rather than emitting a broken URL.
    user_ui_public_base_url: str | None = None
    reviewer_ui_public_base_url: str | None = None
    admin_ui_public_base_url: str | None = None
