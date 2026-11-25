"""Rendering throughput (P95-S1f): the `document.>` consumer processes
several renditions at once, Pillow work runs off the event loop, and a sensor
shows how far the consumer is behind."""

import asyncio
import time
from io import BytesIO
from types import SimpleNamespace

from dms_metrics_client import SensorRegistry
from PIL import Image
from rendering_service import consumer, metrics
from rendering_service.renderers.thumbnail import ThumbnailRenderer
from rendering_service.settings import Settings


async def test_the_document_consumer_runs_with_the_configured_concurrency():
    subscriptions = []

    class Bus:
        async def subscribe(self, subject, handler, *, durable, max_concurrency=1):
            subscriptions.append((subject, durable, max_concurrency))

    await consumer.start_consuming(
        Bus(),
        ["document.>"],
        session_factory=None,
        document_client=None,
        storage=None,
        publish_event=None,
        max_attempts=3,
        max_concurrency=Settings().document_consumer_concurrency,
    )
    assert subscriptions == [("document.>", "rendering-service", 4)]


async def test_a_thumbnail_does_not_block_the_event_loop():
    """Rendering a large image used to run on the event loop; now a ticker
    keeps ticking while it renders."""
    buffer = BytesIO()
    Image.new("RGB", (6000, 6000), (200, 30, 30)).save(buffer, format="PNG")
    data = buffer.getvalue()
    ticks = 0
    done = asyncio.Event()

    async def ticker():
        nonlocal ticks
        while not done.is_set():
            ticks += 1
            await asyncio.sleep(0.005)

    task = asyncio.create_task(ticker())
    started = time.perf_counter()
    output = await ThumbnailRenderer().render(data, filename="gross.png", content_type="image/png")
    elapsed = time.perf_counter() - started
    done.set()
    await task
    assert output.target_content_type == "image/png"
    # Without the thread the ticker would get no turn during the render.
    assert ticks >= max(3, int(elapsed / 0.005 / 4)), (ticks, elapsed)


async def test_the_pending_sensor_reports_the_consumers_backlog():
    class Bus:
        async def consumer_pending(self, stream, durable):
            assert (stream, durable) == ("document", "rendering-service")
            return 7

    registry = SensorRegistry("rendering-service-test", is_active=lambda name: True)
    metrics.build_sensors(registry, SimpleNamespace(state=SimpleNamespace(event_bus=Bus())))
    await registry.run_async_samplers()
    value = registry.collector_registry.get_sample_value("rendering_consumer_pending")
    assert value == 7.0


async def test_libreoffice_profiles_are_reused_and_a_failed_one_is_discarded(tmp_path, monkeypatch):
    from rendering_service.renderers import _libreoffice

    pool = _libreoffice._ProfilePool(2)
    monkeypatch.setattr(pool, "_root", tmp_path)
    first = await pool.acquire()
    second = await pool.acquire()
    assert {first.name, second.name} == {"0", "1"}
    first.mkdir()
    (first / "lock").write_text("x")
    pool.release(first, healthy=True)
    assert (first / "lock").exists()  # kept for the next conversion
    second.mkdir()
    pool.release(second, healthy=False)
    assert not second.exists()  # a failed run's profile is rebuilt
    assert {(await pool.acquire()).name, (await pool.acquire()).name} == {"0", "1"}


async def test_a_text_file_converts_twice_with_one_profile():
    """The real conversion, twice through a one-profile pool: the second run
    reuses the profile the first one built."""
    from rendering_service.renderers import _libreoffice

    pool = _libreoffice._ProfilePool(1)
    original = _libreoffice._profiles
    _libreoffice._profiles = pool
    try:
        timings = []
        for _ in range(2):
            started = time.perf_counter()
            pdf = await _libreoffice.convert_via_libreoffice(
                b"Lasttest\\n", filename="probe.txt", pdf_a=True
            )
            timings.append(time.perf_counter() - started)
            assert pdf.startswith(b"%PDF")
        assert (pool._root / "0").is_dir()
    finally:
        _libreoffice._profiles = original
