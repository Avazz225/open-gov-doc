from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "teamspace-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    folder_service_base_url: str = "http://localhost:8008"
    permission_service_base_url: str = "http://localhost:8004"

    # AD-group invitation (2.5, Post-Roadmap Phase 74 Session 3, ADR
    # 0160/ADR 0217) - `AuthServiceClient.get_group_members`.
    auth_service_base_url: str = "http://localhost:8003"

    monitoring_service_base_url: str = "http://localhost:8026"

    # P55-S1/ADR 0175: `folder-service`'s structural "this folder is now
    # permanently gone" event - the trigger for orphaned-teamspace cleanup.
    subjects: list[str] = ["folder.resource.deleted"]

    # AD-group reconciliation poll loop (2.5, Post-Roadmap Phase 74
    # Session 3, ADR 0160/ADR 0217) - same "poll instead of push" idiom
    # this project uses everywhere a background job needs to stay in sync
    # without a push mechanism (ADR 0020). 300s (5 min): fresh enough that
    # a newly-added AD group member reaches teamspace access without a
    # long wait, not so tight that it hammers Keycloak's group-members
    # endpoint on every tick across every bound teamspace.
    ad_group_reconciliation_poll_interval_seconds: int = 300
