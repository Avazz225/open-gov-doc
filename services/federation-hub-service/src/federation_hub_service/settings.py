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
    # phase (`libs/dms-retry`). Since Phase 40 Session 3, also applies to
    # the return path's hub->origin-installation delivery inside
    # `submit_handover_result` (both legs share this single knob - the
    # plan asked for retry logic "mirroring" the existing one, not a
    # separately tunable one).
    max_handover_delivery_attempts: int = 5
    handover_retry_poll_interval_seconds: float = 60.0

    # DMS-to-DMS XDOMEA handoff (7.4/14.2, Post-Roadmap Phase 43 Session 1,
    # ADR 0147/ADR 0159): the original 15s default assumed small, JSON-
    # shaped BPMN task data - a multi-document case export can plausibly
    # be several megabytes before base64 inflation, and the receiving
    # installation now also does a synchronous archival-service import
    # inside the same request this client waits on. Raised generously
    # rather than finely tuned, same reasoning as the two installation-
    # side client timeouts this mirrors (`workflow_service.settings.
    # federation_hub_request_timeout_seconds`).
    hub_delivery_timeout_seconds: float = 60.0

    # A bounded, explicit ceiling rather than a full backpressure/external-
    # storage redesign of `pending_handover_payloads`/
    # `pending_handover_result_payloads` (ADR 0147's own flagged, NOT fully
    # resolved memory-pressure risk: both dicts hold the full payload in
    # this process's memory for the duration of any retry window). This
    # does not solve unbounded concurrent memory use across many in-flight
    # handovers, but it turns an unbounded per-handover worst case into an
    # explicit, configurable, immediately-rejected one - a proportionate
    # mitigation for this session, not the full storage-backend redesign a
    # future session could still do (see ADR 0159 "Consequences"). ~100MB
    # default: comfortably above ADR 0147's own "plausibly single-digit
    # megabytes before base64 inflation" estimate.
    max_handover_payload_chars: int = 100_000_000

    # Admin UI access (Phase 40 Session 3): federation-hub-service is
    # deliberately NOT registered with `registry-service` (see this
    # class's own docstring - it isn't an internal service of any one
    # installation, other installations' hubs/admins reach it directly
    # too), so it can't be proxied through THIS installation's gateway
    # like every other admin-ui-visible service. `admin-ui` therefore
    # calls it directly via a build-time base URL
    # (`NEXT_PUBLIC_FEDERATION_HUB_BASE_URL`) instead of through
    # `gateway-service` - which means a real browser request needs CORS,
    # unlike every other endpoint in this service (all consumed
    # server-to-server so far). Same convention/defaults as
    # `gateway_service.settings.Settings.cors_allowed_origins`.
    cors_allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    # Sensor concept (Phase 40 Session 4, 10.1) - the first custom sensors
    # this service has ever had (it had none at all before this session,
    # not even the generic HTTP ones - `dms-metrics-client` was not a
    # dependency yet). No `sensors=...` passed to `maybe_start_registration`
    # since this service isn't registered with `registry-service` at all
    # (see this class's own docstring) - the sensor specs therefore aren't
    # discoverable via the registry's catalog, only via a direct `/metrics`
    # scrape, same limitation any non-registered target would have.
    monitoring_service_base_url: str = "http://localhost:8026"
    sensor_sample_interval_seconds: float = 15.0
