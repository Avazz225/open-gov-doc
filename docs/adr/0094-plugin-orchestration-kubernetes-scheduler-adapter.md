# 0094 — plugin-orchestration-service: echter `KubernetesSchedulerAdapter`

**Status:** akzeptiert (P24-S4, siehe `IMPLEMENTATION_PLAN.md`)
**Kontext:** Konzept 3.8, betrifft `plugin-orchestration-service`

## Entscheidung

`platform_scheduler.py` bekommt mit `KubernetesSchedulerAdapter` eine echte Implementierung des
bestehenden `SchedulerAdapter`-Interface (bislang nur `NullSchedulerAdapter`, P10-S0/P10-S2). Wichtig:
das Interface hat genau eine Methode, `try_place(*, cpu_cores, ram_mb) -> str | None` — eine reine
**Platzierungs-Empfehlung** ("welcher Knoten hat Platz"), kein Container-Lifecycle-API. Der Service
startet/stoppt weiterhin keine Container (siehe "Grenzen dieser Ausbaustufe" in
`docs/services/plugin-orchestration-service.md`) — das bleibt unverändert außerhalb seines Scopes, diese
Session ändert daran nichts.

`main.py` wählt `KubernetesSchedulerAdapter` nur, wenn `detect_platform_scheduler()` tatsächlich
`"kubernetes"` liefert (`KUBERNETES_SERVICE_HOST` gesetzt, also ein echter Pod-Kontext) — in dieser
Docker-Compose-Entwicklungsumgebung bleibt `NullSchedulerAdapter` unverändert der reale, gelebte Zustand.

Zwei bewusste Vereinfachungen dieser ersten Version:

1. **Nur In-Cluster-Konfiguration** (`kubernetes.config.load_incluster_config()`), kein Kubeconfig-Pfad
   für eine Out-of-Cluster-Nutzung.
2. **Kapazitätsprüfung ausschließlich gegen `status.allocatable` je Knoten**, nicht gegen die Summe der
   `resources.requests` bereits laufender Pods auf diesem Knoten (kein Pod-Usage-bewusstes Bin-Packing).
   Zusätzlich werden nicht-schedulbare Knoten (`spec.unschedulable`) und nicht-`Ready`-Knoten
   übersprungen — das ist mit reiner Knoten-API-Information ohne Pod-Buchhaltung möglich und ein
   Mindeststandard, den auch ein echter Scheduler beachtet.

Tie-Break bei mehreren passenden Knoten: der mit der meisten frei allokierbaren RAM-Kapazität gewinnt
("most-available", spreizt Last), bei Gleichstand `node_id` als deterministischer Zweit-Tie-Break.

## Begründung

- **Warum nur In-Cluster-Config statt zusätzlich Kubeconfig-Support**: `detect_platform_scheduler()`
  wählt diesen Adapter ausschließlich, wenn `KUBERNETES_SERVICE_HOST` gesetzt ist — dieses Signal
  existiert nur, wenn der Code bereits selbst in einem Pod läuft (offizieller Kubernetes-Mechanismus für
  In-Pod-Service-Discovery). Ein Aufrufkontext, in dem der Adapter aktiv wird, OHNE dass der Prozess in
  einem Pod läuft, existiert im aktuellen Design nicht — ein zusätzlicher Kubeconfig-Ladepfad wäre toter
  Code, der nie den vorgesehenen Aufrufer hätte, plus ein zweiter, ungetesteter Authentifizierungspfad.
  Sollte eine spätere Session einen Bedarf für Out-of-Cluster-Betrieb (z. B. ein Admin-Tool, das gegen ein
  fremdes Cluster spricht) identifizieren, ist das eine bewusste neue Entscheidung, kein stillschweigend
  unterlassenes Feature dieser Session.
- **Warum keine Pod-Usage-bewusste Kapazitätsprüfung (kein echtes Bin-Packing)**: das hätte zusätzlich
  `list_pod_for_all_namespaces()` (oder pro Knoten `list_namespaced_pod` über alle Namespaces) gebraucht,
  jeden Pod-Request aufsummiert und mit dessen Phase (`Running`/`Pending` vs. `Succeeded`/`Failed`)
  korrekt verrechnet — spürbar mehr Komplexität, zusätzliche RBAC-Berechtigungen (Pod-Lese-Rechte über
  alle Namespaces, nicht nur Node-Lese-Rechte) und ohne ein reales Cluster hier nicht sinnvoll gegen echte
  Daten verifizierbar (siehe "Konsequenzen"). `status.allocatable` allein ist eine explizit dokumentierte,
  ehrliche Vereinfachung für eine erste Version, kein verschwiegener Kompromiss — Konsequenz ist unten
  benannt.
- **Warum das trotz der "kein Re-Check downstream"-Falle vertretbar ist**: `decide_placement()` vertraut
  einer Rückgabe von `try_place` vollständig, ohne eigene Kapazitätsprüfung (siehe Docstring dort). Ein
  rein allocatable-basierter Adapter kann also einen tatsächlich ausgelasteten Knoten empfehlen. Das wird
  hier bewusst als dokumentiertes Risiko einer ersten Version akzeptiert (analog zum generellen Prinzip
  dieses Projekts, unfertige Funktionen ehrlich zu benennen statt vorzutäuschen) statt durch verfrühte
  Komplexität "gelöst", die ohne echtes Cluster ohnehin nicht verlässlich verifizierbar wäre.
- **Warum Ready/unschedulable-Filterung trotzdem eingebaut wurde, obwohl das schon über reine
  Vereinfachung hinausgeht**: beide Signale stehen direkt und ohne zusätzliche API-Aufrufe in der bereits
  geladenen `list_node()`-Antwort zur Verfügung — sie zu ignorieren wäre kein Vereinfachungs-Gewinn,
  sondern nur eine unnötig grobe Implementierung, die offensichtlich falsche Knoten empfehlen würde
  (gedrainter/kaputter Knoten).
- **Warum "most-available RAM" statt reines First-Fit als Tie-Break**: der bestehende FFD-Fallback-Pfad
  in `placement.py` nutzt bereits First-Fit (feste `node_id`-Reihenfolge, siehe dortiger Docstring) — für
  den Kubernetes-Zweig wird bewusst eine andere Regel gewählt, weil hier (anders als beim FFD-Fallback)
  keine Pod-Buchhaltung stattfindet: das Bevorzugen des am wenigsten ausgelasteten Knotens reduziert das
  Risiko, einen Knoten zu empfehlen, der in Wahrheit bereits knapp ist, etwas stärker als reines First-Fit
  es täte — kein Ersatz für echte Pod-Buchhaltung, aber eine sinnvolle Verschiebung des Restrisikos.

## Konsequenzen

- **Kein echtes Kubernetes-Cluster in dieser Entwicklungsumgebung** (einziges reales Deploy-Ziel bleibt
  Docker Compose, Phase 26 bringt erst `helm lint`/`helm template`-verifizierte Charts ohne echtes
  Cluster-Deployment) — `KubernetesSchedulerAdapter` ist deshalb ausschließlich gegen einen gemockten
  `kubernetes`-Client getestet (`tests/test_platform_scheduler_kubernetes.py`), niemals gegen einen
  echten API-Server. Das ist eine ehrlich zu benennende Testlücke, keine verschwiegene — siehe
  `docs/services/plugin-orchestration-service.md`.
- **Neue harte Abhängigkeit**: das offizielle `kubernetes`-PyPI-Paket (Python-Client) in
  `services/plugin-orchestration-service/pyproject.toml`.
- **Neue Einstellung** `kubernetes_node_label_selector` (Default leer = alle Knoten) für optionales
  Scoping der Knotenauswahl auf einen Teil des Clusters (z. B. einen dedizierten Plugin-Node-Pool).
- Sollte eine spätere Session (frühestens mit echtem Cluster-Zugriff, außerhalb der heutigen Roadmap)
  Pod-Usage-bewusstes Bin-Packing nachrüsten wollen, ist das ein eigener, bewusster Schritt — nicht
  automatisch Teil dieser Session.
