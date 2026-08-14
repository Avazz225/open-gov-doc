from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    """Shared configuration base. Every service derives from this and
    overrides at least ``service_name`` with a fixed default.
    """

    model_config = SettingsConfigDict(env_prefix="DMS_", env_file=".env", extra="ignore")

    service_name: str
    environment: str = "development"
    log_level: str = "INFO"

    postgres_dsn: str | None = None
    nats_url: str = "nats://localhost:4222"
    otel_exporter_endpoint: str | None = None

    # Self-registration with the registry (3.2a, since P4-S1). Both fields
    # remain empty by default - discovery is an added benefit for services
    # routed by the gateway, not a hard dependency. Only once both are
    # set does a service register itself via dms-registry-client.
    registry_service_base_url: str | None = None
    self_address: str | None = None

    # Installation identity (3a, P13-S1): ONE shared value for the entire
    # installation, passed to every service in the stack via the same two
    # environment variables (`DMS_INSTALLATION_ID`/
    # `DMS_INSTALLATION_DISPLAY_NAME`) - replaces the previously inconsistent
    # practice (only `workflow-service` previously had its own, separately
    # configured `installation_display_name`; every other service had no
    # installation identifier at all). `installation_id` is deliberately NOT
    # the same identifier `workflow-service` uses to register with the
    # Federation Hub (7.4) - that one remains a deliberately separate,
    # randomly generated, opt-in/pseudonymous identifier (see
    # `federation_client.py`), so that federation partners do not
    # automatically learn this installation's internal fleet/license identity.
    installation_id: str = "local-dev"
    installation_display_name: str = "DMS-Installation (Entwicklung)"

    # Fleet agent key (3a, P13-S2): an installation-wide, optional shared
    # secret (`DMS_FLEET_AGENT_API_KEY`) that allows the new, independently
    # operated `fleet-management-service` to trigger exactly two narrowly
    # scoped actions via its own gateway - license installation
    # (`license-service`) and config import (`config-service`), see their
    # respective `_require_*_permission()`. `None` (default) means: no fleet
    # access possible, purely RBAC-based as before - a completely optional,
    # separate building block (3a verbatim).
    fleet_agent_api_key: str | None = None
