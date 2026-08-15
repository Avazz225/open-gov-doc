from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "workflow-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # SLA time monitoring (P6-S2, ADR 0020): poll interval for the boundary
    # timer check. Limits the detection precision of an SLA breach to this
    # interval - no push mechanism available, see ADR 0020.
    sla_poll_interval_seconds: int = 30

    # Retrofit P6-S6 (4.8/authorization): process definitions (BPMN/script
    # task upload) require the domain admin capability `admin.object_config`;
    # the SLA poll loop skips its tick while maintenance mode is active.
    permission_service_base_url: str = "http://localhost:8004"

    # Signature Task (3.10, P6-S7): `POST .../tasks/{id}/complete` requires a
    # valid `signature_id` for a task marked as `taskType=signature`,
    # see `signature_client.py`.
    signature_service_base_url: str = "http://localhost:8017"

    # Sensor concept (10.1, full rollout): base URL of `monitoring-service`,
    # polled by `SensorConfigClient` for the current sensor activation state.
    monitoring_service_base_url: str = "http://localhost:8026"

    # Federation Hub (7.4, P6-S9): opt-in - stays `None` if this
    # installation does not register with the Hub, in which case the
    # Process Designer does not even offer federated process steps
    # (see `federation_client.py`).
    federation_hub_base_url: str | None = None
    # Base URL under which THIS installation is reachable from the Hub via
    # its own gateway (not the internal address of this container - the
    # Hub delivers via `/api/workflow-service/...` so that the gateway
    # JWT/maintenance-mode logic also applies, see ADR 0028).
    installation_gateway_base_url: str = "http://gateway-service:8000"
    # `installation_display_name` has come from `BaseServiceSettings` since
    # P13-S1 (3a) - a shared value for the whole installation instead of a
    # duplicate known only here. `installation_id` deliberately remains its
    # own identifier, independent of that (see `federation_client.py`).
    installation_version: str = "1.0"
    installation_min_compatible_peer_version: str = "1.0"
    # Recipient-side mapping `targetProcessType` (from the sender's
    # federated BPMN step) -> local `process_definition` name. Purely
    # configuration-based, no automatic process catalog reconciliation
    # between installations (see docs/services/workflow-service.md).
    federation_process_type_map: dict[str, str] = {}

    # License mediation (Concept 9.3, P9-S2): workflow-service is the only
    # "application component" (9.1) that actually exists today and queries
    # its own license status from registry-service (not directly from
    # license-service - service isolation, the registry remains the sole
    # mediation point). TTL cache client, modeled on `permission_client.py`.
    license_status_cache_ttl_seconds: float = 15.0

    # BPMN import review gate (4.3, Post-Roadmap Phase 21 Session 4, ADR 0087):
    # subjects that the pure consumer (no own stream, see `consumer.py`)
    # listens on to apply a `workflow.process_definition.import` deferred
    # via the four-eyes principle after approval.
    approval_subjects: list[str] = ["permission.approval.approved"]

    # Connector service tasks (7.1 "triggering a connector call", P12-S2):
    # timeout for the synchronous HTTP call of a `taskType=connector_call`
    # `serviceUrl`. Set generously (unlike a normal web request), since a
    # single step (e.g. migration-service's "copy" phase, see
    # docs/services/migration-service.md) is deliberately synchronous and
    # potentially long-running for a reference implementation.
    connector_call_timeout_seconds: float = 300.0
