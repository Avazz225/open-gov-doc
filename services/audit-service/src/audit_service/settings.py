from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "audit-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Which subjects the Audit Service consumes (concept 3.4: "consumes all
    # events"). Wildcard per producer stream, so new services only need to
    # register here, without code changes. "document.>" was added in P3-S2,
    # since 4.2 explicitly requires complete auditing of force-unlock/
    # conflict copy. "permission.>" was added in P3-S4, since 4.7 explicitly
    # requires complete auditing of scope locks (set/release) - other
    # services (Auth/Storage) follow as needed. "virus_scan.>" was added in
    # P5-S1 - 10.3/5.3 explicitly require auditing of scan results.
    # "rendering.>" was added in P5-S2 - generated/failed renditions are
    # likewise part of traceable document processing. "ocr.>" was added in
    # P5-S3 - 3.9/5.3 explicitly require auditing of OCR results (including
    # needs_review cases). "workflow.>" was added in P6-S1 - process
    # instance start/completion and task completions are part of traceable
    # document processing. "notification.>" was added in P6-S2 - delivery
    # attempts (successful/failed) are likewise part of traceable system
    # actions (5.3). "case.>" was added in P6-S3 - creation/reference
    # change/completion of a circulation folder (2.3) are likewise part of
    # traceable case processing. "auth.>" was added in P6-S5 - superuser
    # break-glass activation/deactivation (4.6) explicitly requires elevated
    # audit priority for the lifecycle itself (see ADR 0023 for the
    # deliberately NOT implemented prioritization of individual third-party
    # actions performed *during* activation). "signature.>" was added in
    # P6-S7 - electronic signatures (3.10) are cryptographically bound to a
    # specific document version and are therefore likewise part of traceable
    # document processing. "favorite.>" was added in P7-S1d - favorite
    # changes should remain traceable in the audit trail like any other user
    # action. "folder.>" was missing entirely until now (a genuine
    # pre-existing bug discovered during the P7-S2 live smoke test -
    # folder-service has had its own event stream since P7-S1b, but was
    # never added to this list) - without this subject, the forensic trace
    # (5.4b) would be blind for all folder actions, directly contradicting
    # the purpose of this session. "query.>" was added in P8-S1 - the Query
    # & Trace Console (6.1) audits every executed query itself
    # (`query.executed`, concept point 5 "complete logging"), proactively
    # added at the same time instead of repeating the "folder.>" mistake a
    # second time. "license.>" was added in P9-S1 - `license-service`'s
    # installation/status-change events (9.2) should likewise end up in the
    # audit trail.
    subjects: list[str] = [
        "registry.>",
        "document.>",
        "permission.>",
        "virus_scan.>",
        "rendering.>",
        "ocr.>",
        "workflow.>",
        "notification.>",
        "case.>",
        "auth.>",
        "signature.>",
        "favorite.>",
        "folder.>",
        "reporting.>",
        "query.>",
        "license.>",
        # "orchestration.>" was added in P10-S1 - the Plugin Orchestration
        # Service (3.8) audits its own placement decisions
        # ("orchestration.placement.decided") via the same event trail.
        "orchestration.>",
        # "monitoring.>" was added in P11-S1 - monitoring-service audits
        # every sensor configuration change ("monitoring.sensor.config_changed",
        # 10.1: disabling security-relevant sensors is itself a
        # security-relevant action).
        "monitoring.>",
        # "teamspace.>" was added in P14-S6 - the new teamspace-service (2.5)
        # audits creation/deletion of a team workspace as well as
        # inviting/removing members (security-relevant, since this is the
        # actual access regime independent of the rest of RBAC).
        "teamspace.>",
        # "mail_connector.>" was added in P15-S3 - the new mail connector
        # (2.5/3.3) audits receipt/assignment/dispatch of external
        # correspondence by the mail room role.
        "mail_connector.>",
    ]

    # Deletion register ledger (10.4, P11-S4): an append-only file on its
    # own Docker volume (deletion-ledger-data), deliberately OUTSIDE the
    # shared Postgres instance - a DB restore (scripts/restore.sh) therefore
    # does not roll this register back too, see
    # docs/operations/backup-restore.md.
    deletion_ledger_path: str = "/deletion-ledger/deletion-register.jsonl"

    monitoring_service_base_url: str = "http://localhost:8026"
