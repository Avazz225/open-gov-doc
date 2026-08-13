# 0077 — `libs/dms-retry`: geteilte Backoff-/Jitter-Mathematik

**Status:** akzeptiert (Session 1 von 7, siehe Phase 20 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 20 Session 1, neue Shared Lib `libs/dms-retry`

## Entscheidung

Neue, sehr kleine Shared Lib `libs/dms-retry` mit genau einer Funktion:
`compute_backoff_seconds(attempt, *, base=1.0, cap=300.0, rng=None) -> float` — "Full Jitter"
Exponentiell-Backoff nach der AWS-Standardformel (`random(0, min(cap, base * 2**attempt))`).

`storage-service`s `replication.py::process_pending` hat bereits das Grundmuster, das der Nutzer für
alle fünf betroffenen Stellen (storage-, archival-, notification-, rendering-/ocr-, federation-hub-
service) will: `ObjectCopy.attempts` + `max_replication_attempts` + Status `"failed_permanent"` nach
Erschöpfung — nur ohne Backoff/Jitter zwischen Versuchen (jeder `process_pending`-Aufruf verarbeitet
sofort alle offenen Kopien erneut, unabhängig davon, wie kurz der letzte Fehlschlag zurückliegt). Diese
Session verallgemeinert ausschließlich die **Zahlenformel**, nicht das Poll-Loop-Muster selbst.

## Begründung

- **Warum eine neue Lib statt einer Erweiterung von `dms-common`**: `dms-common` ist explizit für
  Settings/Logging/OpenTelemetry-Basis beschrieben (`libs/README.md`) — eine Backoff-Formel ist fachlich
  unabhängig davon und wird nur von den fünf Retry-Poll-Loops konsumiert, nicht von jedem Service. Eine
  eigene, sehr kleine Lib hält die Abhängigkeit explizit und optional, statt `dms-common` (von JEDEM
  Service importiert) um eine Funktion zu erweitern, die die meisten davon nie aufrufen.
- **Warum "Full Jitter" statt "Equal Jitter" oder reinem Exponential-Backoff**: verteilt gleichzeitig
  fehlgeschlagene Versuche (z. B. ein Backend-Ausfall, der viele `ObjectCopy`-Zeilen gleichzeitig
  fehlschlagen lässt) am breitesten über das Zeitfenster — vermeidet einen erneuten Thundering-Herd-
  Effekt beim nächsten Poll-Tick, ohne eine zusätzliche Mindestwartezeit (wie bei "Equal Jitter") zu
  erzwingen. Referenz: AWS Architecture Blog, "Exponential Backoff And Jitter".
- **Warum `attempt` 0-indiziert statt 1-indiziert**: passt direkt auf `ObjectCopy.attempts` (beginnt bei
  0, vor dem ersten Versuch) — der Aufrufer muss nicht `attempts - 1` rechnen.
- **Warum `rng` als optionaler Parameter statt globalem `random`-Modul direkt**: erlaubt deterministische
  Unit-Tests (`random.Random(seed)`) ohne `unittest.mock.patch` auf das globale `random`-Modul —
  gleiches Injektions-Prinzip wie `libs/dms-permission-client`s `client`-Parameter für einen
  vorbereiteten `httpx.AsyncClient`.
- **Warum KEIN gemeinsamer Poll-Loop-Rahmen** (bewusst NICHT Teil dieser Session, siehe Roadmap-Plan):
  das Projekt dupliziert Poll-Loops bewusst leichtgewichtig (`_sla_poll_loop`, `_superuser_poll_loop`,
  `_archival_poll_loop` sind je ~20 Zeilen, identisches Try/Except-Weiter-Idiom) statt sie zu
  abstrahieren — eine Rahmen-Abstraktion für fünf strukturell leicht unterschiedliche Loops (manche
  bereits synchron inline, manche schon als Poll-Loop) wäre eine verfrühte Zentralisierung ohne
  echten Mehrwert gegenüber der Kopie einer ~5-Zeilen-Formel.
- **Noch KEIN Konsument in dieser Session**: `compute_backoff_seconds` wird erst ab P20-S2
  (archival-service) tatsächlich verwendet — diese Session legt nur die geteilte Grundlage, damit sie
  in den folgenden vier Sessions identisch (nicht leicht abweichend kopiert) genutzt wird.

## Konsequenzen

- **Tests**: `libs/dms-retry` 6 neue Unit-Tests (Grenzfälle: `attempt=0`, exponentielles Wachstum vor
  dem Cap, Cap-Deckelung bei großem `attempt`, Determinismus mit geseedetem `rng`, negativer `attempt`
  wirft `ValueError`, Default-Parameter liefern einen plausiblen Bereich).
- **`uv.lock`**: `uv lock` ausgeführt, aber unverändert — `dms-retry` hat noch keine Abhängigkeiten und
  keinen Konsumenten, taucht daher erst ab P20-S2 im Auflösungsgraphen auf, wenn `archival-service` es
  als Abhängigkeit deklariert.
- Kein Dockerfile musste geändert werden (`COPY libs/ libs/` erfasst neue Verzeichnisse automatisch,
  gleiches Muster wie bei `dms-permission-client`, P19-S1).
- Kein Live-Verifikationsschritt nötig — reine, zustandslose Bibliotheksfunktion ohne Service-Anbindung.
