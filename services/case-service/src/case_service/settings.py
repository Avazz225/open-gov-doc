from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "case-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # A circulation folder (2.3) starts its lifecycle via a process instance
    # in workflow-service (P6-S1) and resolves its document references there
    # live (dynamic reference to the respective latest version, see repository.py).
    workflow_service_base_url: str = "http://localhost:8014"
    document_service_base_url: str = "http://localhost:8006"
    object_type_service_base_url: str = "http://localhost:8007"
    # RBAC (Post-Roadmap Phase 19 Session 5, ADR 0070) - case-service had
    # no permission check at all until now.
    permission_service_base_url: str = "http://localhost:8004"

    monitoring_service_base_url: str = "http://localhost:8026"

    # Cases whose workflow start ended without a known outcome (P95-S1c):
    # after this grace period - longer than any start can take - the
    # reconciliation adopts the instance if workflow-service has one, or
    # removes the never-started case. Runs at startup and then periodically.
    unstarted_case_grace_seconds: float = 300.0
    unstarted_case_reconcile_interval_seconds: float = 600.0

    # Which subjects case-service consumes (2.3): only the completion of a
    # process instance triggers the completion snapshot of a circulation
    # folder - `workflow.instance.started`/`workflow.task.completed` are
    # irrelevant for this. First consumer of this event at all - workflow-service
    # was previously a pure producer, see docs/services/workflow-service.md "Events".
    subjects: list[str] = ["workflow.instance.completed"]

    # Attribute-level pseudonymization vault (5.2, Phase 58 Session 1,
    # mirrors ADR 0156) - deliberately a SEPARATE key from document-
    # service's/folder-service's own (no shared trust relationship between
    # services' key material, same rationale ADR 0156 itself used).
    attribute_pseudonymization_key: str | None = None

    # P89-S1 (ADR 0235): who may destroy a due case. archival-service's
    # existing disposal-callback capability (role `archival-service-
    # callback`, also gating document-service's disposal callbacks) -
    # reused rather than a new `case.*` capability, because seeded roles are
    # not updated on existing installations (`ensure_domain_admin_roles`
    # only creates missing ones), so a new capability would need a manual
    # migration everywhere.
    disposal_callback_permission: str = "document.disposal_callback"

    # P89-S2: who may trigger the deletion reconciliation after a restore
    # (10.4) - the same realm role document-/folder-service check.
    admin_role: str = "dms-admin"
