"""Plattform-Scheduler bevorzugt, FFD als Fallback (3.8). P10-S0-Entscheidung
(Rueckfrage bei Sessionstart): dieses Repo hat ausschliesslich Docker Compose
als reales Deploy-Ziel (kein Swarm/Kubernetes) - der Plattform-Scheduler-Zweig
wurde deshalb zunaechst als sauberes, konkretes Interface gebaut, aber nur
gegen einen Fake-Adapter getestet, nicht gegen ein echtes Cluster.

P24-S4 rüstet mit `KubernetesSchedulerAdapter` einen echten Adapter fuer genau
dieses Interface nach (siehe ADR 0094). `NullSchedulerAdapter` bleibt der
tatsaechlich gelebte Zustand in DIESER Docker-Compose-Entwicklungsumgebung
(`KUBERNETES_SERVICE_HOST` ist hier nie gesetzt) - `main.py` waehlt den
Kubernetes-Adapter nur, wenn `detect_platform_scheduler()` tatsaechlich einen
Treffer liefert, sonst weiterhin `NullSchedulerAdapter` -> FFD-Fallback."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from kubernetes.client import CoreV1Api

logger = logging.getLogger(__name__)


class SchedulerAdapter(Protocol):
    async def try_place(self, *, cpu_cores: float, ram_mb: float) -> str | None:
        """Liefert eine `node_id`, wenn die Plattform selbst die Platzierung
        uebernommen hat, sonst `None` (FFD-Fallback greift)."""
        ...


class NullSchedulerAdapter:
    """Kein angeschlossener Plattform-Scheduler - immer FFD-Fallback."""

    async def try_place(self, *, cpu_cores: float, ram_mb: float) -> str | None:
        return None


class KubernetesSchedulerAdapter:
    """Echter Plattform-Scheduler-Adapter fuer Kubernetes (P24-S4, siehe ADR
    0094 fuer die vollstaendige Begruendung der hier getroffenen
    Vereinfachungen).

    `try_place` beantwortet ausschliesslich die Platzierungs-Frage des
    `SchedulerAdapter`-Interface ("welcher Knoten HAT Platz") - der Service
    startet dadurch KEINE Container (siehe "Grenzen dieser Ausbaustufe" in
    `docs/services/plugin-orchestration-service.md`), das bleibt unveraendert
    ausserhalb seines Scopes.

    Zwei bewusste Vereinfachungen fuer diese erste Version:

    1. **Nur In-Cluster-Konfiguration** (`kubernetes.config.load_incluster_config`).
       Kein Kubeconfig-Pfad fuer eine Out-of-Cluster-Nutzung, weil
       `detect_platform_scheduler()` diesen Adapter ohnehin nur dann waehlt,
       wenn `KUBERNETES_SERVICE_HOST` gesetzt ist - also der Code bereits IN
       einem Pod laeuft. Ein zusaetzlicher Kubeconfig-Pfad waere fuer diesen
       Aufrufkontext totes Codepfad-Gewicht.
    2. **Kapazitaetspruefung nur gegen `status.allocatable` je Knoten**, NICHT
       gegen die Summe der `resources.requests` bereits laufender Pods auf
       diesem Knoten. Ein Knoten kann also faelschlich als passend gemeldet
       werden, obwohl er durch andere Pods bereits ausgelastet ist. Da
       `decide_placement()` einer Rueckgabe von `try_place` OHNE eigene
       Nachpruefung vertraut (siehe Docstring dort), ist das ein echtes,
       bewusst eingegangenes Risiko fuer diese erste Version - nicht
       stillschweigend uebergangen, siehe ADR 0094.

    Zusaetzlich werden Knoten uebersprungen, die laut `spec.unschedulable`
    cordoned sind oder deren `Ready`-Condition nicht `"True"` ist - beides
    ohne Pod-Buchhaltung direkt aus der Knoten-API ablesbar und ein
    Mindeststandard, den auch ein echter Kubernetes-Scheduler beachtet.
    """

    def __init__(
        self,
        *,
        core_v1_api: CoreV1Api | None = None,
        node_label_selector: str = "",
    ) -> None:
        # `core_v1_api` injizierbar (Tests: gemockter Client, siehe
        # test_platform_scheduler_kubernetes.py). Produktionscode
        # (`main.py`) uebergibt nichts - der echte Client wird lazy beim
        # ersten `try_place`-Aufruf ueber In-Cluster-Config aufgebaut, nicht
        # schon bei Objekterzeugung, damit ein Import/Konstruktion dieser
        # Klasse ausserhalb eines Pods (z. B. beim Modul-Import in Tests)
        # nicht sofort fehlschlaegt.
        self._core_v1_api = core_v1_api
        self._node_label_selector = node_label_selector or None

    def _client(self) -> CoreV1Api:
        if self._core_v1_api is None:
            from kubernetes import client, config

            config.load_incluster_config()
            self._core_v1_api = client.CoreV1Api()
        return self._core_v1_api

    async def try_place(self, *, cpu_cores: float, ram_mb: float) -> str | None:
        api = self._client()
        # Blockierender Netzwerkaufruf des synchronen kubernetes-Clients -
        # in einen Thread ausgelagert, damit er die FastAPI-Event-Loop nicht
        # blockiert (gleiches Prinzip wie an jeder anderen synchronen
        # I/O-Stelle in async-Code).
        node_list = await asyncio.to_thread(api.list_node, label_selector=self._node_label_selector)

        candidates: list[tuple[str, float, float]] = []
        for node in node_list.items:
            capacity = _schedulable_node_capacity(node)
            if capacity is None:
                continue
            node_id, available_cpu_cores, available_ram_mb = capacity
            if cpu_cores <= available_cpu_cores and ram_mb <= available_ram_mb:
                candidates.append((node_id, available_cpu_cores, available_ram_mb))

        if not candidates:
            return None

        # Tie-Break bei mehreren passenden Knoten: der mit der meisten frei
        # allokierbaren RAM-Kapazitaet gewinnt ("most-available" statt reines
        # First-Fit) - verteilt Last eher, statt einen knapp passenden Knoten
        # sofort vollzupacken, was angesichts der oben dokumentierten
        # Vereinfachung (keine Pod-Buchhaltung) das vorsichtigere Verhalten
        # ist. Bei RAM-Gleichstand `node_id` als deterministischer
        # Zweit-Tie-Break.
        candidates.sort(key=lambda c: (-c[2], c[0]))
        best_node_id, _, _ = candidates[0]
        return best_node_id


def _schedulable_node_capacity(node: object) -> tuple[str, float, float] | None:
    """Liefert `(node_id, verfuegbare_cpu_cores, verfuegbarer_ram_mb)` fuer
    einen Knoten aus `CoreV1Api.list_node()`, oder `None`, wenn der Knoten
    cordoned/nicht bereit ist oder keine `node_id`/`allocatable`-Angaben hat.
    "Verfuegbar" meint hier `status.allocatable` (siehe Klassendocstring
    fuer die bewusste Vereinfachung ggue. tatsaechlich laufenden Pods)."""
    from kubernetes.utils import parse_quantity

    metadata = getattr(node, "metadata", None)
    node_id = getattr(metadata, "name", None) if metadata is not None else None
    if not node_id:
        return None

    spec = getattr(node, "spec", None)
    if spec is not None and getattr(spec, "unschedulable", False):
        return None

    node_status = getattr(node, "status", None)
    conditions = getattr(node_status, "conditions", None) or []
    ready = any(
        getattr(condition, "type", None) == "Ready" and getattr(condition, "status", None) == "True"
        for condition in conditions
    )
    if not ready:
        return None

    allocatable = getattr(node_status, "allocatable", None) or {}
    cpu_quantity = allocatable.get("cpu")
    memory_quantity = allocatable.get("memory")
    if cpu_quantity is None or memory_quantity is None:
        return None

    available_cpu_cores = float(parse_quantity(cpu_quantity))
    available_ram_mb = float(parse_quantity(memory_quantity)) / (1024 * 1024)
    return node_id, available_cpu_cores, available_ram_mb


def detect_platform_scheduler() -> str | None:
    """Rein informative Erkennung (3.8) - liefert den Namen einer erkannten
    Plattform oder `None`. `KUBERNETES_SERVICE_HOST` ist das uebliche Signal,
    in einem Kubernetes-Pod zu laufen. Seit P24-S4 existiert fuer einen
    positiven Treffer ein echter Adapter (`KubernetesSchedulerAdapter`,
    s.o.) - `main.py` waehlt ihn basierend auf diesem Rueckgabewert."""
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return "kubernetes"
    return None
