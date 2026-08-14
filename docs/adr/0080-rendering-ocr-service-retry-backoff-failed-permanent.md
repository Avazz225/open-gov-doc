# 0080 — rendering-service & ocr-service: Retry/Backoff, `failed_permanent`

**Status:** akzeptiert (Session 4 von 7, siehe Phase 20 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 20 Session 4, betrifft `rendering-service` und `ocr-service`

## Entscheidung

`Rendition`/`OcrResult` hatten bislang **keinen Retry-Mechanismus**: ein technischer Fehlschlag
(Renderer-Plugin-Fehler, OCR-Engine-Fehler) setzte sofort `status="failed"` (terminal). Beide Services
sind strukturell näher an `notification-service` (ADR 0079) als an `archival-service` (ADR 0078): die
Verarbeitung passiert synchron inline im NATS-Handler (`document.created`/`document.version.created`),
kein mehrphasiger Prozess. Diese Session überträgt das ADR-0079-Muster auf beide Services, mit einer
service-spezifischen Anpassung für `rendering-service`.

1. **Neue Felder** `attempts: int` (Default 0) und `next_retry_at: datetime | None` auf beiden Modellen.
2. **Retry-aware Failure-Recording**: `ocr-service`s neue `repository.record_failure` und
   `rendering-service`s neue `repository.record_failure` (Pendant zu `notification-service`s
   `attempt_delivery`) — unterhalb von `max_ocr_attempts`/`max_rendering_attempts` (Default je 5) bleibt
   `status="failed"` (retry-fähig) mit einem per `compute_backoff_seconds` gesetzten `next_retry_at`;
   erst bei Erschöpfung wechselt `status` auf `failed_permanent`. `ocr-service`s `"skipped"`-Status
   (Wortobergrenze/Content-Type-Positivliste, bewusste Nicht-Fehler-Entscheidung) bleibt davon
   unberührt — nur der `"failed"`-Pfad (`UnreadableDocumentError`, Engine-Exception) ist retry-fähig.
3. **Neue, eigenständige Retry-Poll-Loops** (`_ocr_retry_poll_loop`/`_rendition_retry_poll_loop`,
   Intervall je 60s wie `notification-service`) — der erste Versuch bleibt synchron im NATS-Handler, nur
   die WIEDERHOLUNG läuft asynchron.
4. **Neue Endpunkte** `POST /ocr-results/{id}/retry` und `POST /renditions/{id}/retry` — `409` außer bei
   `failed_permanent`, sonst `repository.reset_for_retry` (setzt `attempts=0`/`error_message=None`/
   `next_retry_at=None` zurück) gefolgt von einem sofortigen synchronen Wiederholungsversuch (gleiche
   Begründung wie ADR 0079: ein einzelner Verarbeitungsschritt, kein mehrphasiger Prozess, ein Admin
   erwartet ein sofortiges Ergebnis). **Der `reset_for_retry`-Schritt ist zwingend** — siehe den bei der
   Live-Verifikation gefundenen Bug unter "Konsequenzen".
5. **`rendering-service`-Besonderheit — Retry pro Renderer, nicht pro Version**: anders als `OcrResult`
   (genau eine autoritative Zeile je Version) hat `Rendition` **mehrere Zeilen je Version** (eine je
   zutreffender Regel aus `select_renderers()`). Ein Wiederholungsversuch darf daher NICHT die gesamte
   `process_version`-Regelkaskade erneut durchlaufen (würde bereits erfolgreiche Renditions unnötig neu
   erzeugen) — neue `renderers.get_renderer_by_type(rendition_type)` schlägt gezielt genau den einen
   betroffenen Renderer nach, eine neue `pipeline.retry_rendition()`-Funktion führt NUR diesen einen
   erneut aus. `ocr-service` braucht dieses Problem nicht: sein Retry ruft schlicht `process_version`
   erneut auf, da es ohnehin nur ein Ergebnis je Version gibt.

## Begründung

- **Warum das notification-service-Muster (ADR 0079) statt des archival-service-Musters (ADR 0078)**:
  beide neuen Services verarbeiten synchron-inline in einem NATS-Handler, nicht als mehrphasige
  Zustandsmaschine — ein Backoff-Warten direkt im Handler würde den Konsumenten blockieren, exakt
  dieselbe Erwägung wie bei `notification-service`.
- **Warum `rendering-service`s Retry gezielt NUR den fehlgeschlagenen Renderer erneut aufruft**: die
  natürliche Mehrzeiligkeit je Version (ein Renderer-Fehlschlag blockiert laut bestehendem Design
  bewusst nicht die übrigen Regeln, siehe `process_version`s Docstring) gilt symmetrisch auch für den
  Retry — ein erneuter voller `process_version`-Durchlauf wäre nicht nur verschwenderisch (bereits
  erfolgreiche Renditions unnötig neu erzeugt), sondern könnte bei einem inzwischen geänderten
  `RENDERERS`-Regelsatz auch zu inkonsistenten Nebenwirkungen führen.
- **Warum `ocr-service`s `"skipped"`-Status NICHT retry-fähig ist**: eine übersprungene Verarbeitung
  (Wortobergrenze, Content-Type-Positivliste) ist eine bewusste, im Audit-Trail sichtbare
  administrative Entscheidung, kein technischer Fehler — sie soll nicht automatisch "repariert" werden,
  nur weil Zeit vergeht (der Grund für den Skip ändert sich nicht durch Zeitablauf, nur durch eine
  bewusste Konfigurationsänderung, die den nächsten regulären Verarbeitungsversuch ohnehin neu bewerten
  würde).
- **Warum KEINE RBAC-Neueinführung für die neuen Retry-Endpunkte**: beide Services haben bereits seit
  P19-S8 ([ADR 0073](0073-ocr-rendering-virus-scan-rbac.md)) `ocr.write`/`rendering.write`-Gates — die
  neuen Endpunkte nutzen die bereits bestehende `_require_ocr_permission`/`_require_rendering_permission`
  mit `access_type="write"`, keine neue Infrastruktur nötig (anders als bei `notification-service`, das
  noch gar keine RBAC-Integration hatte).
- **Warum in `ocr-service`s Retry-Endpunkt/Poll-Tick eine frische Session statt der Request-Session
  verwendet wird**: `process_version`/`retry_rendition` committen über eigene, separate
  `session_factory()`-Aufrufe — ein erneutes `get_*` auf der ursprünglichen Endpunkt-Session würde durch
  deren Identity Map die VOR der Verarbeitung geladene, jetzt veraltete Instanz zurückliefern statt der
  frisch committeten Daten (SQLAlchemys `Session.get()` prüft zuerst den Identity-Cache, keine erneute
  Query bei bereits geladener Instanz).

## Konsequenzen

- **Echter Bug bei der Live-Verifikation gefunden und behoben (beide Services)**: die ursprüngliche
  Fassung der beiden Retry-Endpunkte rief `process_version`/`retry_rendition` direkt auf, OHNE zuvor
  `attempts` zurückzusetzen. Da `record_failure` die `attempts`-Zahl der bereits VORHANDENEN Zeile
  weiterzählt (nicht neu bei 0 beginnt), landete ein erneut fehlschlagender manueller Retry-Versuch
  sofort wieder bei `failed_permanent` (z. B. bei `max_attempts=5`: 5 → 6 ≥ 5) — ein einmal
  `failed_permanent` gewordenes Ergebnis hätte NIE wieder aus diesem Zustand herauskommen können, egal
  wie oft "erneut versuchen" geklickt wurde. Die Unit-Tests hatten dies nicht abgedeckt, weil die
  gewählte, race-freie Testmethode (ein dauerhaft fehlendes Dokument, `DocumentNotFoundError`) den
  `record_failure`-Pfad gar nie erreicht. Erst die Live-Verifikation mit einem echten, tatsächlich neu
  verarbeiteten Dokument deckte es auf. Behoben durch eine neue `repository.reset_for_retry(session,
  result)`-Funktion (setzt `attempts=0`/`error_message=None`/`next_retry_at=None`, lässt `status`
  bewusst unberührt), aufgerufen unmittelbar vor dem erneuten Verarbeitungsversuch in beiden Endpunkten
  — plus je ein neuer Regressionstest auf Repository- UND API-Ebene in beiden Services.
- **Migration bereits laufender Installationen**: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` im
  jeweiligen Lifespan für beide neuen Spalten auf `ocr.ocr_result`/`rendering.rendition`.
- **Tests**: `ocr-service` 51 (vorher ~40, +11: Backoff-Verhalten, `list_due_for_retry`-Filterung,
  `process_version`s `failed_permanent`-Pfad, `reset_for_retry`-Regressionstest, neuer `/retry`-Endpunkt,
  neue `test_main.py` für `_run_retry_tick`). `rendering-service` 44 (vorher ~33, +11: gleiches
  Testmuster, zusätzlich `get_renderer_by_type`/`retry_rendition`-Abdeckung über die Pipeline-/
  API-Tests).
- **Testfixture-Vorsicht bewusst eingehalten**: alle neuen API-/Poll-Tick-Tests, die `TestClient(app)`
  starten (und damit den echten NATS-Konsumenten aktivieren), verwenden bewusst KEIN echtes
  Dokument-Upload für die Retry-Verifikation, sondern ein dauerhaft fehlendes `document_id` (nutzt den
  bereits bestehenden `DocumentNotFoundError`-Abbruchpfad) — ein echtes Upload würde ein reales
  `document.created`-Event auslösen, das vom in derselben Testfunktion gestarteten Konsumenten
  unabhängig verarbeitet würde und mit dem direkten Testaufruf um die `attempts`-Buchführung
  konkurrierte (bei der Entwicklung dieser Session tatsächlich als nicht-deterministischer Testfehler
  beobachtet, siehe `ocr-service`s `test_api.py`/`test_main.py`-Kommentare).
- **Neue `session_factory`-Fixture in beiden `conftest.py`** (fehlte bislang für beide Services, gleiche
  Lücke wie bei `notification-service` vor P20-S3).
- **Live gegen den echten laufenden Stack verifiziert** (Image-Neubau + Neustart beider Services,
  Migration bestätigt, **zweimal** wegen des oben beschriebenen Bugfixes): ein echtes, absichtlich
  korruptes PDF-Dokument über `document-service` hochgeladen — der reguläre synchrone Erstversuch
  scheitert korrekt (`status="failed"`, `attempts=1`, `next_retry_at` gesetzt); nach manuellem Setzen auf
  `failed_permanent` (Poll-Intervall von 60s zu lang für eine zügige Live-Prüfung) bestätigt `POST
  .../retry` in BEIDEN Services vor dem Fix das defekte Verhalten (`attempts` zählt von der erschöpften
  Zahl weiter, bleibt `failed_permanent`) und nach dem Fix das korrekte Verhalten (`attempts=1`,
  `status="failed"`, wieder retry-fähig); `404` für unbekannte Ressourcen in beiden Services bestätigt.
