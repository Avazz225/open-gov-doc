"""Instance replacement and expected replicas (P87-S2, ADR 0241)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from dms_db_base import make_session_factory
from registry_service import replicas
from registry_service.models import ExpectedReplicas, ReplicaAlarmState, ServiceInstance

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
TIMEOUT = 15.0
GRACE = 60.0


def inst(age_seconds: float, status: str = "active", service_type: str = "t") -> ServiceInstance:
    return ServiceInstance(
        instance_id=f"{service_type}-{uuid.uuid4().hex[:8]}",
        service_type=service_type,
        version="0.1.0",
        capabilities=[],
        sensors=[],
        health_endpoint="http://x/healthz",
        address="http://x",
        registered_at=NOW - timedelta(seconds=age_seconds + 10),
        last_heartbeat_at=NOW - timedelta(seconds=age_seconds),
        status=status,
    )


def forget(instances, expected_min=None):
    return replicas.forgettable(
        instances,
        expected_min=expected_min,
        timeout_seconds=TIMEOUT,
        forget_after_seconds=GRACE,
        now=NOW,
    )


# --- Decision 1: one forgetting rule -----------------------------------------


def test_restart_forgets_the_replaced_instance():
    old, new = inst(300), inst(1)
    assert forget([old, new]) == [old]


def test_rolling_restart_forgets_all_old_instances():
    old = [inst(300), inst(400), inst(500)]
    new = [inst(1), inst(2), inst(3)]
    assert set(map(id, forget(old + new, expected_min=3))) == set(map(id, old))


def test_scale_down_forgets_the_surplus():
    dead = [inst(300), inst(400)]
    assert set(map(id, forget([*dead, inst(1)]))) == set(map(id, dead))


def test_crashed_replica_under_an_expectation_stays_visible():
    dead = inst(300)
    assert forget([dead, inst(1), inst(2)], expected_min=3) == []


def test_only_the_deficit_is_kept_and_it_is_the_most_recently_seen():
    newest_dead, older_dead = inst(200), inst(900)
    assert forget([older_dead, newest_dead, inst(1), inst(2)], expected_min=3) == [older_dead]


def test_vanished_type_keeps_everything():
    assert forget([inst(300), inst(900), inst(5000)], expected_min=2) == []


def test_grace_period_protects_a_briefly_paused_instance():
    paused = inst(TIMEOUT + 20)  # unreachable, but for less than the grace
    assert forget([paused, inst(1)]) == []


def test_draining_instances_are_neither_reachable_nor_forgotten():
    draining, dead = inst(1, status="draining"), inst(300)
    # The only reachable instance is draining, so the type counts as R = 0.
    assert forget([draining, dead]) == []


def test_scale_up_has_nothing_to_forget():
    assert forget([inst(1), inst(2), inst(3)]) == []


# --- Decisions 3 and 4: expectation and observed state ------------------------


def test_admin_override_wins_over_the_helm_default():
    override = ExpectedReplicas(service_type="t", min_replicas=3, max_replicas=None)
    expectation = replicas.expectation_for("t", {"t": override}, {"t": {"min": 1, "max": 2}})
    assert (expectation.min, expectation.max, expectation.source) == (3, None, "admin")
    default = replicas.expectation_for("t", {}, {"t": {"min": 2, "max": 5}})
    assert (default.min, default.max, default.source) == (2, 5, "default")
    assert replicas.expectation_for("t", {}, {}).source == "none"


def test_observed_state_covers_every_case():
    two_to_three = replicas.Expectation(2, 3, "admin")
    assert replicas.observed_state(0, two_to_three) == "vanished"
    assert replicas.observed_state(1, two_to_three) == "under"
    assert replicas.observed_state(2, two_to_three) == "ok"
    assert replicas.observed_state(3, two_to_three) == "ok"
    assert replicas.observed_state(4, two_to_three) == "over"
    assert replicas.observed_state(9, replicas.Expectation(2, None, "admin")) == "ok"
    assert replicas.observed_state(0, replicas.Expectation(None, None, "none")) == "unmonitored"


def test_event_for_each_transition():
    def event(old, new):
        return replicas.event_for(replicas.Transition("t", old, new))

    assert event("unmonitored", "vanished") == "registry.replicas.under"
    assert event("ok", "under") == "registry.replicas.under"
    assert event("ok", "over") == "registry.replicas.over"
    assert event("under", "ok") == "registry.replicas.restored"
    assert event("over", "unmonitored") == "registry.replicas.restored"
    assert event("unmonitored", "ok") is None


# --- Database: forgetting and the debounced alarm state -----------------------


async def test_forget_replaced_deletes_rows_and_reports_them(session):
    service_type = f"type-{uuid.uuid4().hex[:8]}"
    old, new = inst(300, service_type=service_type), inst(1, service_type=service_type)
    old.last_heartbeat_at = datetime.now(UTC) - timedelta(seconds=300)
    new.last_heartbeat_at = datetime.now(UTC)
    session.add_all([old, new])
    await session.flush()

    forgotten = await replicas.forget_replaced(
        session, defaults={}, timeout_seconds=TIMEOUT, forget_after_seconds=GRACE
    )

    assert (old.instance_id, service_type) in forgotten
    assert await session.get(ServiceInstance, new.instance_id) is not None
    session.expunge_all()
    assert await session.get(ServiceInstance, old.instance_id) is None


async def test_expected_but_never_registered_type_counts_as_vanished(session):
    service_type = f"type-{uuid.uuid4().hex[:8]}"
    rows = await replicas.overview(
        session, defaults={service_type: {"min": 2, "max": 3}}, timeout_seconds=TIMEOUT
    )
    _, expectation, type_counts = next(r for r in rows if r[0] == service_type)
    assert replicas.observed_state(type_counts.reachable, expectation) == "vanished"


async def test_alarm_commits_only_after_the_debounce(session):
    service_type = f"type-{uuid.uuid4().hex[:8]}"

    async def step(observed, at):
        return await replicas.advance_alarm(
            session, service_type, observed, debounce_seconds=120, now=NOW + timedelta(seconds=at)
        )

    assert await step("under", 0) is None  # pending
    assert await step("under", 60) is None  # still inside the window
    transition = await step("under", 121)
    assert transition == replicas.Transition(service_type, "unmonitored", "under")
    assert await step("under", 200) is None  # no second event for the same state


async def test_flapping_back_resets_the_pending_state(session):
    service_type = f"type-{uuid.uuid4().hex[:8]}"

    async def step(observed, at):
        return await replicas.advance_alarm(
            session, service_type, observed, debounce_seconds=120, now=NOW + timedelta(seconds=at)
        )

    await step("over", 0)
    await step("unmonitored", 30)  # back to the committed state: pending cleared
    assert await step("over", 100) is None  # pending starts again here
    assert await step("over", 200) is None
    assert await step("over", 221) == replicas.Transition(service_type, "unmonitored", "over")


async def test_concurrent_transitions_are_reported_once(engine):
    """Two registry replicas commit the same transition; only the one whose
    conditional UPDATE still sees the old state reports it."""
    service_type = f"type-{uuid.uuid4().hex[:8]}"
    factory = make_session_factory(engine)
    now = datetime.now(UTC)
    async with factory() as setup:
        setup.add(
            ReplicaAlarmState(
                service_type=service_type,
                state="ok",
                since=now,
                pending_state="under",
                pending_since=now - timedelta(seconds=500),
            )
        )
        await setup.commit()

    async with factory() as first, factory() as second:
        won = await replicas.advance_alarm(first, service_type, "under", debounce_seconds=120)
        assert won == replicas.Transition(service_type, "ok", "under")
        # The second replica's UPDATE waits on the row lock until the first
        # commits, then no longer matches `state = 'ok'`.
        racing = asyncio.create_task(
            replicas.advance_alarm(second, service_type, "under", debounce_seconds=120)
        )
        await asyncio.sleep(0.3)
        await first.commit()
        assert await racing is None
        await second.commit()

    async with factory() as check:
        row = await check.get(ReplicaAlarmState, service_type)
        assert row.state == "under"
