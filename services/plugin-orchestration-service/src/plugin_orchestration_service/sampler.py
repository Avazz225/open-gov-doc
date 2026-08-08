"""Eigene, bewusst minimale Ressourcen-Stichprobe (3.8, P10-S0-Befund: die
vollwertige Sensor-Infrastruktur aus 10.1 existiert erst Phase 11). Sampelt
per `psutil` ausschliesslich den eigenen Host - in der real existierenden
Docker-Compose-Umgebung gibt es ohnehin nur diesen einen Knoten. Gleiches
Poll-Loop-Idiom wie `license_service.poll_loop`: ein Fehler in einem Tick
bricht die Schleife nicht ab."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import psutil
from plugin_orchestration_service.models import NODE_ID_SELF, ClusterNode
from plugin_orchestration_service.settings import Settings
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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


async def run_tick(session: AsyncSession) -> None:
    await upsert_node(session, sample_local_node())


async def sampler_loop(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    while True:
        try:
            async with session_factory() as session:
                await run_tick(session)
        except Exception:
            logger.exception("resource_sample_tick_failed")
        await asyncio.sleep(settings.resource_sample_interval_seconds)
