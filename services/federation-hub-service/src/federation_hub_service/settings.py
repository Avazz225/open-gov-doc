from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    """The Federation Hub (7.4) is deliberately **not** an internal service of
    an installation - it therefore does not register with a
    `registry-service` (`BaseServiceSettings.registry_service_base_url`/
    `self_address` remain unused) and has no event-bus producer/consumer
    either (it only logs mediation metadata in its own `handover` table, see
    `docs/services/federation-hub-service.md`). For local
    development/testing it is nonetheless included in
    `infra/docker-compose.yml` (dev-only convenience, see ADR 0028) - an
    operator would run it separately in production."""

    service_name: str = "federation-hub-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Operator secret for `POST /installations/{id}/revoke` (P13-S4,
    # ADR 0039) - deliberately independent of any installation: revocation is
    # explicitly meant for the case where an installation itself can no
    # longer sign in a trustworthy way (compromised key), so it cannot be
    # bound to its own signature. `None` (default) fully locks the endpoint -
    # a hub operator must deliberately set this value to enable revocation
    # at all.
    hub_operator_key: str | None = None

    # Retry/backoff for the initial handover delivery (P20-S5, ADR 0081) -
    # same full-jitter formula as the four other resilience spots of this
    # phase (`libs/dms-retry`). Applies exclusively to the `POST /handovers`
    # delivery to `to_installation_id`, NOT the separate
    # `submit_handover_result` return path.
    max_handover_delivery_attempts: int = 5
    handover_retry_poll_interval_seconds: float = 60.0
