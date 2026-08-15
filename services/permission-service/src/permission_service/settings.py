from dms_common import BaseServiceSettings

ROOT_RESOURCE_ID = "root"


class Settings(BaseServiceSettings):
    service_name: str = "permission-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Provisional contract (see docs/services/permission-service.md): which
    # subjects deliver structure events (resource created/moved/deleted).
    # Expected producer: Folder Service (P3-S3, not yet built) under stream
    # "folder" - simulated via tests until then.
    structure_subjects: list[str] = ["folder.>"]

    # Self-consumption of its own four-eyes approval event (4.3, P6-S4) for
    # action types that permission-service executes itself (scope locks,
    # plus the emergency shutdown trigger since P6-S6) - see
    # approval_consumer.py.
    approval_subjects: list[str] = ["permission.approval.approved"]

    # First cross-service call by this service (P6-S6, 4.8): only the active
    # superuser may lift maintenance mode, whose identity lives in
    # auth-service.
    auth_service_base_url: str = "http://localhost:8003"

    monitoring_service_base_url: str = "http://localhost:8026"

    # Delegation (4.4a, P14-S11): who may revoke a delegation early without
    # being the delegating person themself - same independently configurable
    # role-setting pattern as document-service's
    # `share_link_revoke_admin_role` (P14-S10), even though the default is
    # identical.
    delegation_revoke_admin_role: str = "dms-admin"
