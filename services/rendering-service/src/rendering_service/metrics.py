"""rendering-service sensors (P88-S2b, package B): failed renditions per
kind and state, and a counter of renditions produced per kind. Like OCR
there is no queue; rows are written after processing."""

from __future__ import annotations

import time
from io import BytesIO

from dms_metrics_client import GuardedCounter, SensorRegistry, SensorSpec, throttled
from sqlalchemy import func, select

from rendering_service.models import Rendition

FAILURES = SensorSpec(
    name="rendering.renditions.failed",
    group="reliability",
    cost=3,
    description="Fehlgeschlagene Renditions je Art (thumbnail, pdf_archive, ...) und Zustand",
)
PRODUCED = SensorSpec(
    name="rendering.renditions.produced",
    group="performance",
    cost=1,
    description="Erfolgreich erzeugte Renditions je Art seit Prozessstart",
)

# P88-S2c (package E): an active probe - renders a small generated image to
# PDF/A with the real renderer and reports how long it took, labelled by
# result. Level 5, off by default, and at most once per PROBE_INTERVAL even
# when Prometheus scrapes far more often.
PROBE = SensorSpec(
    name="rendering.probe.duration_seconds",
    group="reliability",
    cost=5,
    description="Test-Konvertierung (Bild zu PDF/A): Dauer in Sekunden, Label result ok/failed",
    default_enabled=False,
)
PROBE_INTERVAL_SECONDS = 300.0
# P95-S1f: how far the `document.>` consumer is behind. Under the load test
# it fell 230,000 events behind without anything showing it.
PENDING = SensorSpec(
    name="rendering.consumer.pending",
    group="capacity",
    cost=2,
    description="Noch nicht verarbeitete document-Ereignisse des Rendering-Konsumenten",
)

_STATE = {"failed": "retrying", "failed_permanent": "permanent"}
_produced: GuardedCounter | None = None


def build_sensors(registry: SensorRegistry, app) -> None:
    global _produced
    _produced = registry.counter(PRODUCED, labelnames=("kind",))

    async def failures() -> dict[tuple[str, ...], float]:
        async with app.state.session_factory() as session:
            rows = await session.execute(
                select(Rendition.rendition_type, Rendition.status, func.count())
                .where(Rendition.status.in_(_STATE))
                .group_by(Rendition.rendition_type, Rendition.status)
            )
        return {(kind, _STATE[status]): float(n) for kind, status, n in rows.all()}

    registry.on_scrape_async(registry.gauge(FAILURES, labelnames=("kind", "state")), failures)

    async def pending() -> float | None:
        bus = getattr(app.state, "event_bus", None)
        if bus is None or not hasattr(bus, "consumer_pending"):
            return None
        value = await bus.consumer_pending("document", "rendering-service")
        return None if value is None else float(value)

    registry.on_scrape_async(registry.gauge(PENDING), pending)
    registry.on_scrape_async(
        registry.gauge(PROBE, labelnames=("result",)),
        throttled(run_probe, min_interval_seconds=PROBE_INTERVAL_SECONDS),
    )


def _probe_image() -> bytes:
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (64, 64), (200, 200, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


async def run_probe() -> dict[tuple[str, ...], float]:
    """One test rendition; never raises - a failure is a reported result."""
    from rendering_service.renderers.pdf_archive import PdfArchiveRenderer

    started = time.monotonic()
    try:
        output = await PdfArchiveRenderer().render(
            _probe_image(), filename="probe.png", content_type="image/png"
        )
        ok = bool(output.data)
    except Exception:
        ok = False
    return {("ok" if ok else "failed",): time.monotonic() - started}


def count_produced(kind: str) -> None:
    if _produced is not None:
        _produced.inc(kind=kind)
