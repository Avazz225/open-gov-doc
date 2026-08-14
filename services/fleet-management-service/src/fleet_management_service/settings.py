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
