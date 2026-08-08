"""Plattform-Scheduler bevorzugt, FFD als Fallback (3.8). P10-S0-Entscheidung
(Rueckfrage bei Sessionstart): dieses Repo hat ausschliesslich Docker Compose
als reales Deploy-Ziel (kein Swarm/Kubernetes) - der Plattform-Scheduler-Zweig
wird deshalb als sauberes, konkretes Interface gebaut, aber nur gegen einen
Fake-Adapter getestet, nicht gegen ein echtes Cluster. `NullSchedulerAdapter`
ist der reale Zustand in diesem Projekt: liefert immer `None`, `placement.py`
faellt dann auf FFD zurueck."""

from __future__ import annotations

import os
from typing import Protocol


class SchedulerAdapter(Protocol):
    async def try_place(self, *, cpu_cores: float, ram_mb: float) -> str | None:
        """Liefert eine `node_id`, wenn die Plattform selbst die Platzierung
        uebernommen hat, sonst `None` (FFD-Fallback greift)."""
        ...


class NullSchedulerAdapter:
    """Kein angeschlossener Plattform-Scheduler - immer FFD-Fallback."""

    async def try_place(self, *, cpu_cores: float, ram_mb: float) -> str | None:
        return None


def detect_platform_scheduler() -> str | None:
    """Rein informative Erkennung (3.8) - liefert den Namen einer erkannten
    Plattform oder `None`. `KUBERNETES_SERVICE_HOST` ist das uebliche Signal,
    in einem Kubernetes-Pod zu laufen. Aktuell existiert **kein** Adapter fuer
    einen positiven Treffer (siehe Moduldocstring) - der Aufrufer soll das
    sichtbar loggen, statt es still zu ignorieren."""
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return "kubernetes"
    return None
