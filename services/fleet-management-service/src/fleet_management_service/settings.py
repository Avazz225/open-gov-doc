from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    """The fleet-/license-management service (3a) is deliberately **not** an
    internal service of a single installation - an operator overseeing
    multiple installations runs it independently of them (same architectural
    pattern as `federation-hub-service`, ADR 0028). It therefore does not
    register with a `registry-service`
    (`BaseServiceSettings.registry_service_base_url`/`self_address` remain
    unused) and has no event-bus producer/consumer. For local
    development/testing it is nonetheless included in
    `infra/docker-compose.yml` (dev-only convenience) - an operator would run
    it separately in production, see
    docs/services/fleet-management-service.md."""

    service_name: str = "fleet-management-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Timeout for outgoing calls to a managed installation via its gateway
    # (status query/license push/provisioning) - more generous than plain
    # health checks, since `POST .../config/import` performs multiple
    # owner-service calls in sequence (see config-service).
    agent_request_timeout_seconds: float = 30.0

    # P54-S1/ADR 0172: gates every endpoint except `/healthz` via
    # `Authorization: Bearer <fleet_operator_key>` - the same
    # `hub_operator_key` mechanism `federation-hub-service` already uses
    # (ADR 0039/0162). `None` by default - fully locks the API until an
    # operator deliberately configures this, same fail-closed default.
    fleet_operator_key: str | None = None

    # P67-S1: this service is now called directly by a browser (admin-ui),
    # bypassing the gateway (which normally handles CORS for every other
    # admin-ui-visible service) - same reasoning/same default as
    # `federation-hub-service.settings.Settings.cors_allowed_origins`, never
    # needed before this session since no admin-ui ever called this service.
    cors_allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]
