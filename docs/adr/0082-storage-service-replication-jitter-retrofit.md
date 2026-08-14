# 0082 — storage-service: Full-Jitter-Backoff für die Replikations-Retry-Queue

**Status:** akzeptiert (Session 6 von 7, siehe Phase 20 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 20 Session 6, betrifft `storage-service`

## Entscheidung

`storage-service` hatte bereits das vollständige Grundmuster, das die vier anderen Sessions dieser Phase
(ADR 0078–0081) neu einführen mussten: `ObjectCopy.attempts` + `max_replication_attempts` (Default 5) +
Terminalstatus `failed_permanent` (siehe [ADR 0004](0004-storage-redundancy-scope.md)). Es fehlte nur ein
einzelnes Element — Full-Jitter-Backoff zwischen Versuchen. `POST /replication/process-pending` griff
bislang bei JEDEM Aufruf sofort erneut jede `status IN ("pending", "failed")`-Zeile auf, ohne jede
Wartezeit. Diese Session rüstet `libs/dms-retry`s `compute_backoff_seconds` (gleiche Formel wie an den
vier anderen Resilienz-Stellen dieser Phase) nach.

1. **Neues Feld** `next_retry_at: datetime | None` auf `ObjectCopy` (kein neuer Terminalstatus nötig -
   `failed_permanent` existiert bereits).
2. **`repository.list_pending_copies`** filtert jetzt zusätzlich auf
   `next_retry_at IS NULL OR next_retry_at <= now()` — `NULL` (neue Zeile, noch nie fehlgeschlagen) bleibt
   immer sofort fällig.
3. **`repository.record_copy`** bekommt einen neuen `next_retry_at`-Parameter, der — anders als
   `retention_until`s "nur bei explizitem Wert übernehmen"-Muster — wie `last_error` UNBEDINGT gesetzt
   wird (Default `None`): ein erfolgreicher oder frischer Schreibversuch braucht keine verbleibende
   Backoff-Zeit, ein evtl. zuvor gesetzter Wert muss verschwinden.
4. **`replication.process_pending`** berechnet bei einem `"failed"`-Ergebnis (egal ob wegen fehlender
   Quellkopie oder eines Backend-Schreibfehlers) über eine neue `_next_retry_at(attempts)`-Hilfsfunktion
   ein per Full-Jitter-Backoff gesetztes `next_retry_at`; bei `"failed_permanent"` explizit `None` (kein
   weiterer automatischer Versuch).
5. **Kein CronJob in dieser Session** — der Plan verschiebt die eigentliche periodische Ausführung
   (`/replication/process-pending`, `/object-verify/{key}/all`) explizit auf **P26-S4** (Helm-Chart-
   CronJob-Template), da Phase 26 noch nicht existiert. [ADR 0004](0004-storage-redundancy-scope.md)s
   Entscheidung "expliziter Endpunkt statt In-Prozess-Hintergrundtask" (Testbarkeit/Neustart-Semantik)
   bleibt unverändert gültig — diese Session ändert nur, WANN eine Zeile innerhalb eines Laufs erneut
   aufgegriffen wird, nicht WER den Lauf auslöst.

## Begründung

- **Warum kein neuer Poll-Loop wie bei ADR 0079–0081**: `storage-service` hat bewusst KEINEN
  In-Prozess-Hintergrundtask (siehe ADR 0004) — die Retry-Queue wird ausschließlich durch externe Aufrufe
  von `POST /replication/process-pending` abgearbeitet. Jitter ändert nur, welche Zeilen ein solcher
  Aufruf tatsächlich aufgreift (fällige vs. noch wartende), nicht die Aufruf-Architektur selbst.
- **Warum `next_retry_at` unbedingt statt bedingt gesetzt wird** (Abweichung vom `retention_until`-Muster
  in derselben Funktion): `retention_until` ist ein einmal gesetzter, seltener geänderter Wert, der bei
  Zwischenaufrufen (Fixity-Checks, Fehlerfällen) erhalten bleiben MUSS. `next_retry_at` beschreibt dagegen
  den unmittelbar bevorstehenden nächsten Versuch dieser EINEN Operation — bei jedem Aufruf neu und
  korrekt zu bestimmen, nie ein Altwert, den ein späterer Aufruf versehentlich stehen lassen dürfte (ein
  erfolgreicher Schreibversuch, der einen alten Backoff-Wert übrig ließe, wäre ein eigener kleiner Bug).
- **Warum `verify_all_copies` (Fixity-Check) NICHT ebenfalls Backoff bekommt**: eine per Fixity-Check als
  `"failed"` markierte Zeile (Prüfsummenabweichung) ist ein anderer Fehlerfall als ein technischer
  Replikations-Fehlschlag — sie hatte bereits einmal erfolgreich repliziert und wird durch einen erneuten
  `process_pending`-Lauf ohnehin überschrieben/neu versucht; ein Backoff dafür ist nicht Teil des in
  dieser Session behobenen, ursprünglich in `libs/dms-retry`s eigenem Docstring benannten Gaps und wäre
  Scope-Creep über eine reine Jitter-Nachrüstung hinaus.

## Konsequenzen

- **Migration bereits laufender Installationen**: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS
  next_retry_at` im Lifespan auf `storage.object_copy`.
- **Bestehender Test musste angepasst werden**: `test_process_pending_marks_permanently_failed_after_max_attempts`
  rief `process_pending` bislang zweimal in einer engen Schleife auf und erwartete, dass der zweite Aufruf
  dieselbe Zeile sofort wieder aufgreift — mit echtem Jitter (auch wenn `attempt=0`s Backoff nur
  `uniform(0, 1)` Sekunden beträgt) ist das nicht mehr deterministisch garantiert. Der Test setzt
  `next_retry_at` jetzt zwischen den beiden Aufrufen explizit in die Vergangenheit zurück (gleiches Muster
  wie die Tick-Tests der vier anderen Services dieser Phase).
- **Neue Tests**: 113 (vorher 109, +4) — `next_retry_at` wird nach einem Fehlschlag gesetzt und verhindert
  ein sofortiges erneutes Aufgreifen; nach künstlichem Vorspulen des Zeitstempels wird die Zeile wieder
  aufgegriffen; `list_pending_copies` filtert eine noch nicht fällige Zeile aus, lässt eine fällige
  (anderer Key) unverändert durch.
- **`POST /replication/process-pending`/`GET /object-verify/{key}/all` bleiben bis P26-S4 weiterhin ohne
  jeden Scheduler** — dieselbe, in `docs/services/storage-service.md` "Offene Punkte" bereits dokumentierte
  Lücke, jetzt präzisiert um "Jitter bereits vorhanden, nur die externe Triggerung fehlt noch".
- **Live gegen den echten laufenden Stack verifiziert** (Image-Neubau + Neustart, Migration bestätigt):
  ein echtes Objekt hochgeladen, per direktem SQL-Insert eine zweite `object_copy`-Zeile mit einem im
  Container nicht konfigurierten `backend_id` angelegt (erzeugt einen echten `KeyError` beim
  Backend-Dict-Zugriff in `process_pending`, kein Mocking) — der erste `POST
  /replication/process-pending`-Aufruf liefert `attempts=1` und ein gesetztes `next_retry_at` in der
  nahen Zukunft; ein SOFORT folgender zweiter Aufruf liefert `processed=0` (die Zeile ist noch nicht
  fällig); nach manuellem Zurücksetzen von `next_retry_at` in die Vergangenheit greift ein dritter Aufruf
  die Zeile wieder auf (`attempts=2`) — bestätigt den vollen Jitter-Zyklus 1:1 gegen den echten Container.
