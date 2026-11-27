"""Several workflow-service replicas (P101-S1, ADR 0273): each poll tick and
the startup seeding run in one replica at a time. "Replicas" here are separate
engines, i.e. separate database connections, as in two processes."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from dms_db_base import build_engine, make_session_factory
from workflow_service import main, repository, spiff_adapter
from workflow_service.models import BusinessCalendar

# Read like conftest sets it (importing from conftest is unreliable here).
DSN = os.environ["DMS_POSTGRES_DSN"]


@pytest.fixture
async def replicas():
    engines = [build_engine(DSN), build_engine(DSN)]
    yield [make_session_factory(e) for e in engines]
    for engine in engines:
        await engine.dispose()


@pytest.fixture
def published(monkeypatch):
    events: list[tuple[str, str | None]] = []

    async def fake_publish(event_type, subject, payload, *, actor=None):
        events.append((event_type, subject))

    monkeypatch.setattr(main, "publish_event", fake_publish)
    return events


async def test_the_lock_belongs_to_one_transaction(replicas):
    first, second = replicas
    async with first() as a, second() as b:
        assert await repository.try_replica_lock(a, repository.SLA_TICK_LOCK)
        assert not await repository.try_replica_lock(b, repository.SLA_TICK_LOCK)
        # Other keys are independent.
        assert await repository.try_replica_lock(b, repository.CLAIM_EXPIRY_TICK_LOCK)
        await a.commit()  # end of the tick: released
        await b.commit()
        assert await repository.try_replica_lock(b, repository.SLA_TICK_LOCK)
        await b.rollback()


async def test_two_replicas_fire_a_due_timer_once(replicas, published, boundary_timer_bpmn):
    first, second = replicas
    async with first() as session:
        definition = await repository.create_process_definition(
            session, name="EskalationReplikas", bpmn_xml=boundary_timer_bpmn, process_id=None
        )
        instance = await repository.start_instance(
            session, definition.id, created_by="alice", business_key=None, initial_data={}
        )
        await session.commit()
    await asyncio.sleep(0.1)

    lanes = AsyncMock()
    await asyncio.gather(
        main.run_sla_tick(first, lanes),
        main.run_sla_tick(second, lanes),
    )
    # A later tick of either replica finds nothing new.
    await main.run_sla_tick(second, lanes)
    assert published == [("workflow.task.escalated", instance.id)]


async def test_two_replicas_notify_an_abandoned_claim_once(
    replicas, published, manual_task_bpmn, monkeypatch
):
    first, second = replicas
    async with first() as session:
        definition = await repository.create_process_definition(
            session, name="ClaimReplikas", bpmn_xml=manual_task_bpmn, process_id=None
        )
        instance = await repository.start_instance(
            session, definition.id, created_by="alice", business_key=None, initial_data={}
        )
        [task] = await repository.get_ready_tasks(session, instance.id)
        claim = await repository.claim_task(session, instance.id, task.id, "dora")
        claim.claimed_at = datetime.now(UTC) - timedelta(hours=100)
        await session.commit()

    # Without the lock both replicas read the due claim before either one
    # commits; this makes that interleaving certain instead of lucky.
    original = repository.list_claims_due_for_expiry_notice
    arrived = asyncio.Event()
    readers = 0

    async def list_then_wait(session, **kwargs):
        nonlocal readers
        rows = await original(session, **kwargs)
        readers += 1
        if readers == 2:
            arrived.set()
        try:
            await asyncio.wait_for(arrived.wait(), timeout=0.5)
        except TimeoutError:
            pass  # the other replica didn't read - the lock held it off
        return rows

    monkeypatch.setattr(repository, "list_claims_due_for_expiry_notice", list_then_wait)
    await asyncio.gather(main.run_claim_expiry_tick(first), main.run_claim_expiry_tick(second))
    assert readers == 1
    await main.run_claim_expiry_tick(first)
    assert published == [("workflow.task_claim.abandoned", instance.id)]


async def test_replicas_starting_together_seed_the_template_once(replicas):
    async def start(factory):
        async with factory() as session:
            await repository.ensure_circulation_template(session)
            await session.commit()

    await asyncio.gather(*(start(factory) for factory in replicas))
    async with replicas[0]() as session:
        versions = await repository.list_process_definitions(
            session, name=repository.CIRCULATION_TEMPLATE_NAME
        )
    assert len(versions) == 1


async def test_every_tick_picks_up_a_calendar_another_replica_wrote(replicas, published):
    first, second = replicas
    # Written by "another replica": only the row, not this process's cache.
    async with first() as session:
        session.add(
            BusinessCalendar(
                name="Bayern",
                non_working_dates=["2026-12-08"],
                is_default=True,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()
    assert "Bayern" not in spiff_adapter._business_calendars

    async with second() as session:
        holder = await repository.try_replica_lock(session, repository.SLA_TICK_LOCK)
        assert holder
        # Even the replica that doesn't get the tick refreshes its calendar.
        await main.run_sla_tick(first, AsyncMock())
        await session.rollback()
    assert spiff_adapter._business_calendars["Bayern"] == {datetime(2026, 12, 8).date()}
    assert spiff_adapter._default_calendar_name == "Bayern"
