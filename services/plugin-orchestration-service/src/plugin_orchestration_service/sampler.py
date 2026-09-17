"""Eigene, bewusst minimale Ressourcen-Stichprobe (3.8, P10-S0-Befund).
Sampelt per `psutil` ausschliesslich den eigenen Host - in der real
existierenden Docker-Compose-Umgebung gibt es ohnehin nur diesen einen
Knoten. Gleiches Poll-Loop-Idiom wie `license_service.poll_loop`: ein
Fehler in einem Tick bricht die Schleife nicht ab.

Seit Phase 40 Session 4 speist dieselbe Stichprobe zusaetzlich zwei echte
Sensoren (10.1, `metrics.py`) - additiv, nicht ersetzend: `ClusterNode`
(unten) bleibt die von `placement.py`s Scheduling gelesene Quelle, ein
`GuardedGauge` hat keinen Rueckgabewert und koennte diese Rolle nicht
uebernehmen."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import psutil
from dms_metrics_client import GuardedGauge
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plugin_orchestration_service.models import NODE_ID_SELF, ClusterNode
from plugin_orchestration_service.settings import Settings

logger = logging.getLogger(__name__)


def sample_local_node() -> dict:
    virtual_memory = psutil.virtual_memory()
    return {
        "cpu_cores": float(psutil.cpu_count(logical=True) or 1),
        "total_ram_mb": virtual_memory.total / (1024 * 1024),
        "cpu_usage_percent": psutil.cpu_percent(interval=None),
        "available_ram_mb": virtual_memory.available / (1024 * 1024),
        "sampled_at": datetime.now(UTC),
    }


async def upsert_node(session: AsyncSession, values: dict, *, node_id: str = NODE_ID_SELF) -> None:
    """Atomarer Upsert (``ON CONFLICT DO UPDATE``) - bewusst NICHT das
    sonst im Projekt uebliche Get-oder-Erzeuge-Muster (z. B.
    `registry_service.repository.register`), weil hier - anders als dort -
    tatsaechlich zwei nebenlaeufige Schreiber auf dieselbe Zeile treffen
    koennen (der Hintergrund-Loop UND z. B. ein Test, der denselben Knoten
    mit eigenen Werten seedet) - ein Get-dann-Insert waere dort eine echte
    Race Condition (`UniqueViolationError`), kein nur theoretisches Risiko."""
    stmt = insert(ClusterNode).values(node_id=node_id, **values)
    stmt = stmt.on_conflict_do_update(index_elements=[ClusterNode.node_id], set_=values)
    await session.execute(stmt)
    await session.commit()


async def run_tick(
    session: AsyncSession,
    *,
    cpu_usage_gauge: GuardedGauge | None = None,
    available_ram_gauge: GuardedGauge | None = None,
) -> None:
    """`cpu_usage_gauge`/`available_ram_gauge` (Phase 40 Session 4,
    optional/default `None` for backward compatibility with existing
    direct callers/tests) are set from the SAME sampled values already
    computed for the `ClusterNode` upsert below - not a second,
    independent psutil sample. See `metrics.py`'s module docstring for
    why this is additive rather than a replacement of the upsert."""
    values = sample_local_node()
    await upsert_node(session, values)
    if cpu_usage_gauge is not None:
        cpu_usage_gauge.set(values["cpu_usage_percent"])
    if available_ram_gauge is not None:
        available_ram_gauge.set(values["available_ram_mb"])


async def sampler_loop(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    cpu_usage_gauge: GuardedGauge | None = None,
    available_ram_gauge: GuardedGauge | None = None,
) -> None:
    while True:
        try:
            async with session_factory() as session:
                await run_tick(
                    session,
                    cpu_usage_gauge=cpu_usage_gauge,
                    available_ram_gauge=available_ram_gauge,
                )
        except Exception:
            logger.exception("resource_sample_tick_failed")
        await asyncio.sleep(settings.resource_sample_interval_seconds)
