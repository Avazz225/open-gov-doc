import asyncio
import os
import uuid
from unittest.mock import patch

import nats.aio.msg
import pytest
from dms_eventbus_client import NatsEventBusClient, SubjectNotFoundError
from nats.errors import MsgAlreadyAckdError

NATS_URL = os.environ.get("TEST_NATS_URL", "nats://localhost:4222")


@pytest.fixture
async def client():
    stream = f"test_{uuid.uuid4().hex[:8]}"
    bus = NatsEventBusClient(NATS_URL, stream)
    await bus.connect()
    yield bus, stream
    await bus.close()


async def test_publish_subscribe_roundtrip(client):
    bus, stream = client
    received: list[bytes] = []
    got_message = asyncio.Event()

    async def handler(payload: bytes) -> None:
        received.append(payload)
        got_message.set()

    await bus.subscribe(f"{stream}.ping", handler, durable="test-consumer")
    await asyncio.sleep(0.2)  # Subscription muss beim Server aktiv sein, bevor publiziert wird
    await bus.publish(f"{stream}.ping", b"hello")

    await asyncio.wait_for(got_message.wait(), timeout=5)
    assert received == [b"hello"]


async def test_pure_consumer_without_stream_ownership():
    """Ein Konsument wie der Audit Service besitzt keinen eigenen Stream (ADR 0001) -
    er abonniert das Subject eines fremden, bereits existierenden Streams direkt.
    """
    stream = f"test_{uuid.uuid4().hex[:8]}"
    producer = NatsEventBusClient(NATS_URL, stream)
    await producer.connect()

    consumer = NatsEventBusClient(NATS_URL, ensure_stream=False)
    await consumer.connect()

    received: list[bytes] = []
    got_message = asyncio.Event()

    async def handler(payload: bytes) -> None:
        received.append(payload)
        got_message.set()

    try:
        await consumer.subscribe(f"{stream}.created", handler, durable="consumer-test")
        await asyncio.sleep(0.2)
        await producer.publish(f"{stream}.created", b"payload")

        await asyncio.wait_for(got_message.wait(), timeout=5)
        assert received == [b"payload"]
    finally:
        await producer.close()
        await consumer.close()


def test_ensure_stream_true_requires_stream_name():
    with pytest.raises(ValueError, match="stream ist erforderlich"):
        NatsEventBusClient(NATS_URL, stream=None, ensure_stream=True)


async def test_max_concurrency_default_processes_sequentially(client):
    """Default (kein `max_concurrency`) muss sich exakt wie vor P5b-S5
    verhalten - kein zweiter Handler-Aufruf beginnt, bevor der vorige (inkl.
    Ack) abgeschlossen ist."""
    bus, stream = client
    concurrent_count = 0
    max_observed = 0
    processed = []

    async def handler(payload: bytes) -> None:
        nonlocal concurrent_count, max_observed
        concurrent_count += 1
        max_observed = max(max_observed, concurrent_count)
        await asyncio.sleep(0.1)
        processed.append(payload)
        concurrent_count -= 1

    await bus.subscribe(f"{stream}.ping", handler, durable="test-consumer")
    await asyncio.sleep(0.2)
    for i in range(3):
        await bus.publish(f"{stream}.ping", str(i).encode())

    found = await _wait_until(lambda: len(processed) == 3)
    assert found
    assert max_observed == 1


async def test_max_concurrency_above_one_processes_in_parallel(client):
    """P5b-S5 (ocr-service "Verarbeitungs-Batch-Size"): ein `max_concurrency`
    > 1 lässt mehrere Handler-Aufrufe gleichzeitig laufen, statt sie seriell
    abzuarbeiten."""
    bus, stream = client
    concurrent_count = 0
    max_observed = 0
    processed = []

    async def handler(payload: bytes) -> None:
        nonlocal concurrent_count, max_observed
        concurrent_count += 1
        max_observed = max(max_observed, concurrent_count)
        await asyncio.sleep(0.2)
        processed.append(payload)
        concurrent_count -= 1

    await bus.subscribe(f"{stream}.ping", handler, durable="test-consumer", max_concurrency=3)
    await asyncio.sleep(0.2)
    for i in range(3):
        await bus.publish(f"{stream}.ping", str(i).encode())

    found = await _wait_until(lambda: len(processed) == 3)
    assert found
    assert max_observed > 1


async def test_max_concurrency_failed_handler_does_not_block_capacity(client):
    """Ein fehlschlagender Handler darf die freigewordene Kapazität nicht
    blockieren (`finally`-Block in `_run`) - sonst würde ein einziger
    dauerhaft kaputter Dokumenttyp irgendwann die gesamte Batch-Verarbeitung
    zum Stillstand bringen. Bewusst kein Test auf tatsächliche NATS-
    Redelivery hier (Default-`ack_wait` macht das ~30s langsam/flaky) - die
    "kein Ack bei Fehler"-Logik ist dieselbe wie im unveränderten
    sequentiellen Pfad (`limit <= 1`)."""
    bus, stream = client
    processed = []

    async def handler(payload: bytes) -> None:
        if payload == b"boom":
            raise RuntimeError("simulierter Fehler")
        await asyncio.sleep(0.05)
        processed.append(payload)

    await bus.subscribe(
        f"{stream}.ping", handler, durable="test-consumer", max_concurrency=2, deliver_new=True
    )
    await asyncio.sleep(0.2)
    await bus.publish(f"{stream}.ping", b"boom")
    for i in range(3):
        await bus.publish(f"{stream}.ping", str(i).encode())

    found = await _wait_until(lambda: len(processed) == 3)
    assert found
    assert set(processed) == {b"0", b"1", b"2"}


async def test_ack_failure_after_successful_handler_is_logged_not_crashed(client):
    """A real `msg.ack()` failure (e.g. `MsgAlreadyAckdError` on a
    redelivery race) previously escaped `_run`'s fire-and-forget task
    unguarded, surfacing only as an orphaned "Task exception was never
    retrieved" - the handler itself already succeeded, so this must not
    crash the subscription or the event loop, just get logged."""
    bus, stream = client
    processed = []
    loop_exceptions = []
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(lambda loop, context: loop_exceptions.append(context))

    async def handler(payload: bytes) -> None:
        processed.append(payload)

    with patch.object(nats.aio.msg.Msg, "ack", side_effect=MsgAlreadyAckdError()):
        await bus.subscribe(f"{stream}.ping", handler, durable="test-consumer", max_concurrency=2)
        await asyncio.sleep(0.2)
        await bus.publish(f"{stream}.ping", b"hello")

        found = await _wait_until(lambda: len(processed) == 1)
        assert found
        # The orphaned-task path (if still broken) would only surface via
        # the loop's exception handler on the next event-loop tick.
        await asyncio.sleep(0.1)

    assert loop_exceptions == []


async def _wait_until(predicate, timeout_seconds: float = 5.0, interval: float = 0.05) -> bool:
    elapsed = 0.0
    while elapsed < timeout_seconds:
        if predicate():
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return False


async def test_subscribe_without_existing_stream_raises_subject_not_found():
    """Konsument startet, bevor irgendein Producer den Stream angelegt hat -
    passiert z. B., wenn der Permission Service vor dem Folder Service startet."""
    consumer = NatsEventBusClient(NATS_URL, ensure_stream=False)
    await consumer.connect()

    async def handler(payload: bytes) -> None:
        pass

    try:
        with pytest.raises(SubjectNotFoundError):
            await consumer.subscribe(
                f"nonexistent_{uuid.uuid4().hex[:8]}.>", handler, durable="consumer-test"
            )
    finally:
        await consumer.close()


async def test_consumer_pending_counts_unseen_and_unacked_messages(client):
    """P95-S1f: the backlog a sensor reports - messages not yet delivered plus
    delivered but unacknowledged ones; `None` for an unknown consumer."""
    bus, stream = client
    gate = asyncio.Event()

    async def slow_handler(payload: bytes) -> None:
        await gate.wait()

    try:
        await bus.subscribe(f"{stream}.work", slow_handler, durable="pending-probe")
        for n in range(5):
            await bus.publish(f"{stream}.work", f"job {n}".encode())
        assert await _wait_until_async(
            lambda: bus.consumer_pending(stream, "pending-probe"), lambda v: v == 5
        )
        gate.set()
        assert await _wait_until_async(
            lambda: bus.consumer_pending(stream, "pending-probe"), lambda v: v == 0
        )
        assert await bus.consumer_pending(stream, "no-such-consumer") is None
    finally:
        gate.set()
        await bus._js.delete_stream(stream)


async def _wait_until_async(read, predicate, timeout_seconds: float = 5.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate(await read()):
            return True
        await asyncio.sleep(0.05)
    return False


# --- Queue groups (P95-S2a) --------------------------------------------------


async def test_two_instances_share_a_durable_without_losing_or_doubling(client):
    """Two instances of one service (a replica, a rolling update's surge pod)
    subscribe the same durable: both start, and each message reaches exactly
    one of them. Before P95-S2a the second died with "consumer is already
    bound to a subscription"."""
    bus, stream = client
    second = NatsEventBusClient(NATS_URL, ensure_stream=False)
    await second.connect()
    seen: dict[str, list[bytes]] = {"a": [], "b": []}

    def handler_for(name):
        async def handler(payload: bytes) -> None:
            seen[name].append(payload)

        return handler

    try:
        await bus.subscribe(f"{stream}.job", handler_for("a"), durable="shared")
        await second.subscribe(f"{stream}.job", handler_for("b"), durable="shared")
        await asyncio.sleep(0.2)
        for n in range(40):
            await bus.publish(f"{stream}.job", f"job {n}".encode())
        assert await _wait_until(lambda: len(seen["a"]) + len(seen["b"]) >= 40)
        await asyncio.sleep(0.3)
        everything = seen["a"] + seen["b"]
        assert sorted(everything) == sorted(f"job {n}".encode() for n in range(40))
        assert seen["a"] and seen["b"], "both instances should get a share"
    finally:
        await second.close()
        await bus._js.delete_stream(stream)


async def test_a_durable_from_before_queue_groups_is_migrated_at_its_position(client):
    """An old-style durable (push, no deliver group) that already acked two of
    six messages: the first subscribe recreates it as a queue consumer and
    delivers exactly the four that were left."""
    bus, stream = client
    js = bus._js
    old_inbox = bus._nc.new_inbox()
    await js.add_consumer(
        stream,
        nats.js.api.ConsumerConfig(
            durable_name="legacy",
            deliver_subject=old_inbox,
            filter_subject=f"{stream}.legacy",
            ack_policy=nats.js.api.AckPolicy.EXPLICIT,
        ),
    )
    for n in range(6):
        await bus.publish(f"{stream}.legacy", f"msg {n}".encode())
    # The old version of the service handled and acked the first two.
    acked = 0
    done = asyncio.Event()

    async def old_handler(msg):
        nonlocal acked
        if acked < 2:
            await msg.ack()
            acked += 1
            if acked == 2:
                done.set()

    old_sub = await bus._nc.subscribe(old_inbox, cb=old_handler)
    await asyncio.wait_for(done.wait(), timeout=5)
    await old_sub.unsubscribe()
    await asyncio.sleep(0.2)

    received: list[bytes] = []

    async def handler(payload: bytes) -> None:
        received.append(payload)

    try:
        await bus.subscribe(f"{stream}.legacy", handler, durable="legacy")
        assert await _wait_until(lambda: len(received) >= 4)
        await asyncio.sleep(0.3)
        assert received == [f"msg {n}".encode() for n in range(2, 6)]
        info = await js.consumer_info(stream, "legacy")
        assert info.config.deliver_group == "legacy"
        # A second subscribe (another instance) joins instead of migrating again.
        await bus.subscribe(f"{stream}.legacy", handler, durable="legacy")
        assert (await js.consumer_info(stream, "legacy")).config.deliver_group == "legacy"
    finally:
        await bus._js.delete_stream(stream)


# --- Dead letters (P99-S1, ADR 0270) --------------------------------------------


async def _dead_letters_for(bus, stream: str, consumer: str) -> list:
    """The DLQ messages for one consumer, read with an ephemeral pull."""
    from dms_eventbus_client.nats_backend import dead_letter_subject

    js = bus._js
    sub = await js.pull_subscribe(dead_letter_subject(stream, consumer), durable=None)
    try:
        return await sub.fetch(10, timeout=2)
    except Exception:  # noqa: BLE001 - nothing there
        return []
    finally:
        await sub.unsubscribe()


def test_retry_delay_grows_and_is_capped():
    from dms_eventbus_client.nats_backend import MAX_RETRY_DELAY_SECONDS, retry_delay

    assert [retry_delay(n) for n in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 8.0]
    assert retry_delay(30) == MAX_RETRY_DELAY_SECONDS


async def test_a_message_that_keeps_failing_is_dead_lettered_and_the_next_flows():
    """Until P99 a failing handler was redelivered forever and stalled its
    consumer (Phase 51, P98-S1)."""
    stream = f"test_{uuid.uuid4().hex[:8]}"
    bus = NatsEventBusClient(NATS_URL, stream, max_deliver=2)
    await bus.connect()
    good = asyncio.Event()
    attempts: list[bytes] = []

    async def handler(payload: bytes) -> None:
        attempts.append(payload)
        if payload == b"poison":
            raise ValueError("kaputt")
        good.set()

    durable = f"dlq-test-{uuid.uuid4().hex[:6]}"
    try:
        await bus.subscribe(f"{stream}.x", handler, durable=durable)
        await asyncio.sleep(0.2)
        await bus.publish(f"{stream}.x", b"poison")
        await bus.publish(f"{stream}.x", b"fine")
        await asyncio.wait_for(good.wait(), timeout=10)
        # The poison message: first delivery, one retry (after 1 s), then DLQ.
        for _ in range(40):
            if attempts.count(b"poison") >= 2 and await bus.dead_letter_counts():
                break
            await asyncio.sleep(0.25)
        assert attempts.count(b"poison") == 2

        [letter] = await _dead_letters_for(bus, stream, durable)
        assert letter.data == b"poison"
        assert letter.headers["Dms-Dlq-Subject"] == f"{stream}.x"
        assert letter.headers["Dms-Dlq-Consumer"] == durable
        assert letter.headers["Dms-Dlq-Deliveries"] == "2"
        assert letter.headers["Dms-Dlq-Error"] == "ValueError: kaputt"
        counts = await bus.dead_letter_counts()
        assert counts[(stream, durable)] == 1

        # Acknowledged: nothing pending for the consumer any more.
        assert await bus.consumer_pending(stream, durable) == 0
    finally:
        await bus._js.purge_stream("DLQ", subject=f"dlq.{stream}.>")
        await bus.close()


async def test_a_transient_failure_recovers_within_the_attempts():
    stream = f"test_{uuid.uuid4().hex[:8]}"
    bus = NatsEventBusClient(NATS_URL, stream, max_deliver=3)
    await bus.connect()
    done = asyncio.Event()
    calls = 0

    async def handler(payload: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("Abhängigkeit startet gerade neu")
        done.set()

    durable = f"dlq-transient-{uuid.uuid4().hex[:6]}"
    try:
        await bus.subscribe(f"{stream}.y", handler, durable=durable)
        await asyncio.sleep(0.2)
        await bus.publish(f"{stream}.y", b"once")
        await asyncio.wait_for(done.wait(), timeout=10)
        assert calls == 2
        assert (stream, durable) not in await bus.dead_letter_counts()
    finally:
        await bus.close()


async def test_concurrent_handlers_dead_letter_too():
    stream = f"test_{uuid.uuid4().hex[:8]}"
    bus = NatsEventBusClient(NATS_URL, stream, max_deliver=1)
    await bus.connect()

    async def handler(payload: bytes) -> None:
        raise RuntimeError("immer")

    durable = f"dlq-concurrent-{uuid.uuid4().hex[:6]}"
    try:
        await bus.subscribe(f"{stream}.z", handler, durable=durable, max_concurrency=4)
        await asyncio.sleep(0.2)
        await bus.publish(f"{stream}.z", b"a")
        for _ in range(40):
            if (stream, durable) in await bus.dead_letter_counts():
                break
            await asyncio.sleep(0.25)
        assert (await bus.dead_letter_counts())[(stream, durable)] == 1
    finally:
        await bus._js.purge_stream("DLQ", subject=f"dlq.{stream}.>")
        await bus.close()


async def test_a_dead_letter_replays_to_its_own_consumer_only():
    """P99-S2: the replay reaches the consumer that gave up, not every
    consumer of the subject; once it succeeds the letter is gone."""
    stream = f"test_{uuid.uuid4().hex[:8]}"
    bus = NatsEventBusClient(NATS_URL, stream, max_deliver=1)
    await bus.connect()
    ready = False
    failing_calls: list[bytes] = []
    other_calls: list[bytes] = []
    replayed = asyncio.Event()

    async def failing_until_ready(payload: bytes) -> None:
        failing_calls.append(payload)
        if not ready:
            raise LookupError("Eltern-Ordner fehlt noch")
        replayed.set()

    async def other(payload: bytes) -> None:
        other_calls.append(payload)

    durable = f"dlq-replay-{uuid.uuid4().hex[:6]}"
    try:
        await bus.subscribe(f"{stream}.r", failing_until_ready, durable=durable)
        await bus.subscribe(f"{stream}.r", other, durable=f"{durable}-other")
        await asyncio.sleep(0.3)
        await bus.publish(f"{stream}.r", b"event-1")
        for _ in range(40):
            letters = [x for x in await bus.list_dead_letters() if x["stream"] == stream]
            if letters:
                break
            await asyncio.sleep(0.25)
        [letter] = letters
        assert letter["consumer"] == durable and letter["subject"] == f"{stream}.r"
        detail, payload = await bus.get_dead_letter(letter["seq"])
        assert payload == b"event-1" and detail["error"].startswith("LookupError")

        ready = True
        assert await bus.replay_dead_letter(letter["seq"]) is not None
        await asyncio.wait_for(replayed.wait(), timeout=10)
        assert failing_calls == [b"event-1", b"event-1"]
        assert other_calls == [b"event-1"]  # not delivered twice to the healthy one
        assert not [x for x in await bus.list_dead_letters() if x["stream"] == stream]
    finally:
        await bus._js.purge_stream("DLQ", subject=f"dlq.{stream}.>")
        await bus.close()


async def test_a_replay_that_fails_again_returns_under_its_original_name_and_discard_removes_it():
    stream = f"test_{uuid.uuid4().hex[:8]}"
    bus = NatsEventBusClient(NATS_URL, stream, max_deliver=1)
    await bus.connect()

    async def always(payload: bytes) -> None:
        raise ValueError("bleibt kaputt")

    durable = f"dlq-again-{uuid.uuid4().hex[:6]}"

    async def letters_for_stream():
        return [x for x in await bus.list_dead_letters() if x["stream"] == stream]

    try:
        await bus.subscribe(f"{stream}.q", always, durable=durable)
        await asyncio.sleep(0.3)
        await bus.publish(f"{stream}.q", b"x")
        for _ in range(40):
            if await letters_for_stream():
                break
            await asyncio.sleep(0.25)
        [first] = await letters_for_stream()
        await bus.replay_dead_letter(first["seq"])
        for _ in range(40):
            again = await letters_for_stream()
            if again and again[0]["seq"] != first["seq"]:
                break
            await asyncio.sleep(0.25)
        [second] = await letters_for_stream()
        assert second["consumer"] == durable  # not "<durable>-replay"
        assert second["subject"] == f"{stream}.q"
        assert second["stream_seq"] == first["stream_seq"]
        assert second["replayed_from"] == str(first["seq"])

        assert await bus.discard_dead_letter(second["seq"]) is not None
        assert await letters_for_stream() == []
        assert await bus.discard_dead_letter(second["seq"]) is None
    finally:
        await bus._js.purge_stream("DLQ", subject=f"dlq.{stream}.>")
        await bus.close()
