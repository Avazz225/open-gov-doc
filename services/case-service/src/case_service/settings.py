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

    # Which subjects case-service consumes (2.3): only the completion of a
    # process instance triggers the completion snapshot of a circulation
    # folder - `workflow.instance.started`/`workflow.task.completed` are
    # irrelevant for this. First consumer of this event at all - workflow-service
    # was previously a pure producer, see docs/services/workflow-service.md "Events".
    subjects: list[str] = ["workflow.instance.completed"]
