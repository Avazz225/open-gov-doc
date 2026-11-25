"""Ready-task index (P95-S1b): `models.ReadyTask` and
`ProcessInstance.waits_on_timer` stay in step with the engine state on every
state change, agree with a full scan, are filled in for instances from before
the index, and make `GET /tasks` cost the same with thousands of running
instances as with a handful."""

import asyncio
import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, select, text, update
from workflow_service import repository, spiff_adapter
from workflow_service.main import app
from workflow_service.models import ProcessInstance, ReadyTask

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _read(name: str) -> str:
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


OPS = _read("instance_ops.bpmn")
OPS_V2 = OPS.replace('name="Ablegen"', 'name="Archivieren"')


async def _assert_index_matches(session, instance_id: str) -> list[spiff_adapter.TaskInfo]:
    """The index rows of one instance against the engine state (the truth)."""
    instance = await repository.get_instance(session, instance_id)
    wf = spiff_adapter.deserialize(instance.workflow_state)
    truth = [] if spiff_adapter.is_completed(wf) else spiff_adapter.ready_manual_tasks(wf)
    indexed = await repository.indexed_ready_tasks(session, instance_id)
    assert [(t.id, t.name, t.lane, t.extensions) for t in indexed] == [
        (t.id, t.name, t.lane, t.extensions) for t in truth
    ]
    assert [t.data for t in indexed] == [repository._json_safe(t.data) for t in truth]
    assert instance.waits_on_timer is (
        not spiff_adapter.is_completed(wf) and spiff_adapter.waits_on_timer(wf)
    )
    return indexed


async def _definition(session, name: str, xml: str):
    return await repository.create_process_definition(
        session, name=name, bpmn_xml=xml, process_id=None
    )


async def _start(session, definition_id: int, **initial_data):
    return await repository.start_instance(
        session, definition_id, created_by="alice", business_key=None, initial_data=initial_data
    )


async def test_start_and_complete_keep_the_index(session):
    definition = await _definition(session, "Ops", OPS)
    instance = await _start(session, definition.id, betreff="Antrag")
    [pruefen] = await _assert_index_matches(session, instance.id)
    assert pruefen.name == "Prüfen" and pruefen.data["betreff"] == "Antrag"

    await repository.complete_task(
        session, instance.id, pruefen.id, completed_by="alice", data={"ok": True}
    )
    [freigeben] = await _assert_index_matches(session, instance.id)
    assert freigeben.name == "Freigeben" and freigeben.data["ok"] is True

    await repository.complete_task(session, instance.id, freigeben.id, completed_by="a", data={})
    [ablegen] = await _assert_index_matches(session, instance.id)
    await repository.complete_task(session, instance.id, ablegen.id, completed_by="a", data={})
    assert instance.status == "completed"
    assert await _assert_index_matches(session, instance.id) == []


async def test_skip_send_back_and_migrate_keep_the_index(session):
    v1 = await _definition(session, "Ops", OPS)
    v2 = await _definition(session, "Ops", OPS_V2)
    instance = await _start(session, v1.id)
    [pruefen] = await _assert_index_matches(session, instance.id)

    await repository.skip_task(
        session, instance.id, pruefen.id, actor="admin", reason="test", expected_version=None
    )
    [freigeben] = await _assert_index_matches(session, instance.id)
    assert freigeben.name == "Freigeben"

    done = await repository.completed_tasks(session, instance.id)
    target = next(t for t in done if t.name == "Prüfen")
    await repository.send_back(session, instance.id, target.id, expected_version=None)
    [again] = await _assert_index_matches(session, instance.id)
    assert again.name == "Prüfen"

    await repository.complete_task(session, instance.id, again.id, completed_by="a", data={})
    await repository.complete_task(
        session,
        instance.id,
        (await repository.indexed_ready_tasks(session, instance.id))[0].id,
        completed_by="a",
        data={},
    )
    await repository.migrate_instance(session, instance.id, v2.id, expected_version=None)
    [archivieren] = await _assert_index_matches(session, instance.id)
    assert archivieren.name == "Archivieren"


async def test_a_task_that_stays_ready_keeps_its_row(session, boundary_timer_bpmn):
    """A boundary timer firing (non-interrupting) saves the state; the task it
    sits on stays ready, so its row and `ready_since` stay too."""
    definition = await _definition(session, "Eskalation", boundary_timer_bpmn)
    instance = await _start(session, definition.id, escalation_email="x@example.com")
    assert instance.waits_on_timer is True
    [before] = (
        await session.execute(select(ReadyTask).where(ReadyTask.instance_id == instance.id))
    ).scalars()
    since, row_id = before.ready_since, before.id

    await asyncio.sleep(0.1)
    [result] = await repository.advance_timers(session)
    assert len(result.fired) == 1
    await _assert_index_matches(session, instance.id)
    [after] = (
        await session.execute(select(ReadyTask).where(ReadyTask.instance_id == instance.id))
    ).scalars()
    assert (after.id, after.ready_since) == (row_id, since)


async def test_the_sla_tick_reads_only_instances_with_a_timer(
    session, boundary_timer_bpmn, monkeypatch
):
    plain = await _definition(session, "Ops", OPS)
    timed = await _definition(session, "Eskalation", boundary_timer_bpmn)
    for _ in range(20):
        await _start(session, plain.id)
    with_timer = await _start(session, timed.id, escalation_email="x@example.com")
    await session.commit()

    reads = []
    original = spiff_adapter.deserialize
    monkeypatch.setattr(
        spiff_adapter, "deserialize", lambda state: reads.append(1) or original(state)
    )
    results = await repository.advance_timers(session)
    assert [r.instance.id for r in results] == [with_timer.id]
    assert len(reads) == 1


async def test_index_agrees_with_a_full_scan(session, boundary_timer_bpmn):
    """Many instances in different states, then the whole index against a
    scan of every running instance's engine state."""
    ops = await _definition(session, "Ops", OPS)
    timed = await _definition(session, "Eskalation", boundary_timer_bpmn)
    instances = [await _start(session, ops.id, n=n) for n in range(12)]
    instances += [await _start(session, timed.id, escalation_email="x@example.com")]
    for n, instance in enumerate(instances[:12]):
        for _ in range(n % 4):  # 0..3 completions: some finish, some don't
            tasks = await repository.indexed_ready_tasks(session, instance.id)
            if tasks:
                await repository.complete_task(
                    session, instance.id, tasks[0].id, completed_by="a", data={}
                )
    await asyncio.sleep(0.1)
    await repository.advance_timers(session)
    await session.commit()

    scan = []
    running = await repository.list_instances(session, status="running")
    for instance in running:
        wf = spiff_adapter.deserialize(instance.workflow_state)
        scan += [(instance.id, t.id, t.name) for t in spiff_adapter.ready_manual_tasks(wf)]
    indexed = await repository.list_indexed_ready_tasks(session, limit=1000)
    assert sorted((r.instance.id, r.task.id, r.task.name) for r in indexed) == sorted(scan)
    assert len({i for i, _, _ in scan}) == 10  # 12 - 3 completed + the timer instance
    # Rows of completed instances are gone, not just hidden by the join.
    total = await session.scalar(select(func.count()).select_from(ReadyTask))
    assert total == len(scan)


async def test_backfill_indexes_old_instances_without_moving_their_version(
    session, engine, boundary_timer_bpmn
):
    ops = await _definition(session, "Ops", OPS)
    timed = await _definition(session, "Eskalation", boundary_timer_bpmn)
    plain = await _start(session, ops.id)
    with_timer = await _start(session, timed.id, escalation_email="x@example.com")
    broken = await _start(session, ops.id)
    await session.commit()
    # As saved before P95-S1b: no flag, no rows; plus one unreadable blob.
    await session.execute(delete(ReadyTask))
    await session.execute(update(ProcessInstance).values(waits_on_timer=None))
    await session.execute(
        update(ProcessInstance)
        .where(ProcessInstance.id == broken.id)
        .values(workflow_state="{not json")
    )
    await session.commit()
    versions = dict(
        (await session.execute(select(ProcessInstance.id, ProcessInstance.workflow_version))).all()
    )

    factory = repository.async_sessionmaker(engine, expire_on_commit=False)
    assert await repository.backfill_ready_task_index(factory, batch_size=2) == 3
    assert await repository.backfill_ready_task_index(factory) == 0  # idempotent

    async with factory() as fresh:
        for instance in (plain, with_timer):
            await _assert_index_matches(fresh, instance.id)
        assert (await repository.get_instance(fresh, broken.id)).waits_on_timer is True
        assert await repository.indexed_ready_tasks(fresh, broken.id) == []
        after = dict(
            (
                await fresh.execute(select(ProcessInstance.id, ProcessInstance.workflow_version))
            ).all()
        )
    assert after == versions


# --- GET /tasks over the index ---------------------------------------------


@pytest.fixture
def client():
    with TestClient(app, headers={"X-DMS-Principal": "workflow-service-tests"}) as c:
        yield c


def _upload(client, admin_headers, xml: str = OPS) -> int:
    response = client.post(
        "/process-definitions",
        data={"name": f"Ops-{uuid.uuid4().hex[:6]}"},
        files={"bpmn_xml": ("ops.bpmn", xml, "application/xml")},
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _start_via_api(client, definition_id: int) -> str:
    response = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "carla"},
        headers={"X-DMS-Principal": "carla"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _count_statements(client, path: str, headers: dict | None = None):
    statements = []

    def on_execute(*_args):
        statements.append(1)

    sync_engine = app.state.engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", on_execute)
    try:
        start = time.perf_counter()
        response = client.get(path, headers=headers or {})
        elapsed = time.perf_counter() - start
    finally:
        event.remove(sync_engine, "before_cursor_execute", on_execute)
    assert response.status_code == 200, response.text
    return response.json(), len(statements), elapsed


def test_task_list_cost_does_not_grow_with_running_instances(client, admin_headers):
    definition_id = _upload(client, admin_headers)
    first = _start_via_api(client, definition_id)
    for _ in range(4):
        _start_via_api(client, definition_id)

    small, small_statements, _ = _count_statements(client, "/tasks?limit=50")
    assert len(small) == 5

    client.portal.call(_clone_coro, first, 3000)
    large, large_statements, elapsed = _count_statements(client, "/tasks?limit=50")
    assert len(large) == 50
    assert large_statements == small_statements
    assert elapsed < 1.0, f"GET /tasks took {elapsed:.2f} s with 3,005 running instances"

    # Paging is in SQL and stable: two pages, no overlap, newest first.
    page2, _, _ = _count_statements(client, "/tasks?limit=50&offset=50")
    assert not {t["instance_id"] for t in large} & {t["instance_id"] for t in page2}

    # "Meine Aufgaben" too: unbound lanes, so every task is the caller's.
    mine, _, elapsed = _count_statements(
        client, "/tasks?eligible=me&limit=50", headers={"X-DMS-Principal": "carla"}
    )
    assert len(mine) == 50 and all(t["eligible"] for t in mine)
    assert elapsed < 1.0


async def _clone_coro(source_id: str, copies: int) -> None:
    async with app.state.engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO workflow.process_instance
                    (id, process_definition_id, business_key, status, workflow_state,
                     created_by, created_at, updated_at, workflow_version, waits_on_timer)
                SELECT gen_random_uuid()::text, process_definition_id, 'clone-' || n,
                       status, workflow_state, created_by,
                       created_at - n * interval '1 second', updated_at, 1, false
                FROM workflow.process_instance, generate_series(1, :copies) AS n
                WHERE id = :source
                """
            ),
            {"source": source_id, "copies": copies},
        )
        await conn.execute(
            text(
                """
                INSERT INTO workflow.ready_task
                    (instance_id, task_id, position, name, lane, task_type, extensions,
                     data, ready_since, instance_created_at)
                SELECT p.id, r.task_id, r.position, r.name, r.lane, r.task_type,
                       r.extensions, r.data, r.ready_since, p.created_at
                FROM workflow.process_instance p, workflow.ready_task r
                WHERE p.business_key LIKE 'clone-%' AND r.instance_id = :source
                """
            ),
            {"source": source_id},
        )


def test_claimed_tasks_of_others_are_not_mine(client, admin_headers):
    definition_id = _upload(client, admin_headers)
    taken = _start_via_api(client, definition_id)
    free = _start_via_api(client, definition_id)
    [task] = client.get(f"/instances/{taken}/tasks").json()
    response = client.post(
        f"/instances/{taken}/tasks/{task['id']}/claim",
        json={},
        headers={"X-DMS-Principal": "dora"},
    )
    assert response.status_code in (200, 201), response.text

    mine = client.get("/tasks?eligible=me", headers={"X-DMS-Principal": "carla"}).json()
    assert [t["instance_id"] for t in mine] == [free]
    dora = client.get("/tasks?eligible=me", headers={"X-DMS-Principal": "dora"}).json()
    assert {t["instance_id"] for t in dora} == {taken, free}
    everyone = {t["instance_id"]: t for t in client.get("/tasks").json()}
    assert everyone[taken]["claimed_by"] == "dora"
