from datetime import UTC, datetime

from dms_metrics_client import SensorRegistry
from federation_hub_service import metrics, repository
from federation_hub_service.models import Handover


def _always_active_registry() -> SensorRegistry:
    return SensorRegistry("federation-hub-service-test", is_active=lambda name: True)


def test_sensor_declarations_includes_both_retry_cache_sensors_and_http():
    names = {declaration["name"] for declaration in metrics.sensor_declarations()}
    assert "federation_hub.retry_cache.forward_pending" in names
    assert "federation_hub.retry_cache.result_pending" in names
    assert "http.requests" in names


async def _create_handover(session, handover_id: str) -> None:
    handover = Handover(
        id=handover_id,
        from_installation_id="from-installation",
        to_installation_id="to-installation",
        process_type="test-process",
        status="pending_retry",
        created_at=datetime.now(UTC),
    )
    session.add(handover)
    await session.flush()


async def test_build_samplers_reports_the_live_cache_sizes(session, session_factory):
    """Post-Roadmap Phase 73 Session 3 (ADR 0213): both caches are now
    persisted in `handover_retry_payload`, not plain in-process dicts - the
    sampler functions must report the actual `COUNT(*) ... WHERE leg = ...`
    against the DB, not a `len(dict)`."""
    registry = _always_active_registry()
    forward_gauge, result_gauge = metrics.build_sensor_registry(registry)
    samplers = metrics.build_samplers(forward_gauge, result_gauge, session_factory)

    await _create_handover(session, "handover-a")
    await _create_handover(session, "handover-b")
    await _create_handover(session, "handover-c")
    await repository.save_pending_payload(session, "handover-a", leg="forward", payload={})
    await repository.save_pending_payload(session, "handover-b", leg="forward", payload={})
    await repository.save_pending_payload(session, "handover-c", leg="result", payload={})
    await session.commit()

    forward_value = await samplers[metrics.FORWARD_RETRY_CACHE_DEPTH.name][1]()
    result_value = await samplers[metrics.RESULT_RETRY_CACHE_DEPTH.name][1]()

    assert forward_value == 2.0
    assert result_value == 1.0


async def test_build_samplers_reflects_cache_changes_live(session, session_factory):
    registry = _always_active_registry()
    forward_gauge, result_gauge = metrics.build_sensor_registry(registry)
    samplers = metrics.build_samplers(forward_gauge, result_gauge, session_factory)
    compute_forward = samplers[metrics.FORWARD_RETRY_CACHE_DEPTH.name][1]

    assert await compute_forward() == 0.0

    await _create_handover(session, "handover-1")
    await repository.save_pending_payload(
        session, "handover-1", leg="forward", payload={"encrypted_payload": "opaque"}
    )
    await session.commit()
    assert await compute_forward() == 1.0

    await repository.delete_pending_payload(session, "handover-1", leg="forward")
    await session.commit()
    assert await compute_forward() == 0.0
