import asyncio
import os
import uuid

import pytest
from dms_eventbus_client import NatsEventBusClient, SubjectNotFoundError

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
