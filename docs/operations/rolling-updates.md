# Rolling Updates (Konzept 10.5)

Betriebsverfahren, kein Service — erstes Dokument dieser Kategorie im
Projekt. Beschreibt, wie ein einzelner Service in diesem Docker-Compose-
Deploy-Ziel (P10-S0-Befund: keine Orchestrierungsplattform vorhanden) ohne
Unterbrechung aktualisiert wird, unter Wiederverwendung des in P10-S2
gebauten Drain-Mechanismus (`registry-service`).

## Grundmechanismus: Parallelbetrieb statt In-Place-Austausch

`scripts/rolling-update.sh <service>` führt die in 10.5 beschriebene
Choreografie aus:

1. Bestehende Instanz-ID(s) des Service-Typs von der Registry abfragen.
2. Neues Image bauen.
3. Einen zweiten, temporären Container ("Canary") **ohne Host-Port-
   Publish** starten (`docker compose run -d --no-deps --name
   dms-<service>-canary`) — kein Konflikt mit dem laufenden, port-
   gebundenen Compose-Service. `DMS_SELF_ADDRESS` wird auf den
   Container-eigenen DNS-Namen überschrieben, da ein `docker compose
   run`-Container nicht automatisch den geteilten Service-Netzwerk-Alias
   bekommt.
4. Pollen (`GET /instances/{service_type}`), bis die Registry den Canary
   als neue, gesunde, `"active"` Instanz meldet — der Health-/Readiness-
   Check aus 10.5 ("durchläuft dort einen Health-/Readiness-Check, bevor
   sie überhaupt als einsatzbereit gilt") wird durch die bereits
   vorhandene Heartbeat-/`healthy`-Berechnung der Registry realisiert,
   kein neues Protokoll.
5. Alte Instanz(en) draining setzen (`POST /instances/{id}/drain`, P10-S2)
   — nimmt keine neuen Anfragen mehr an, laufende Vorgänge laufen
   unangetastet weiter (`gateway-service`s `InstanceResolver` schließt sie
   vom Routing neuer Anfragen aus).
6. Gnadenfrist abwarten (`--drain-grace-seconds`, Default 30s).
7. Alte, reguläre Compose-Instanz stoppen/entfernen — `docker compose stop`
   sendet `SIGTERM`, die Service-eigene Lifespan-Shutdown-Logik
   dereigistriert sich dabei bereits selbst über `dms-registry-client`
   (`DELETE /instances/{id}`), kein expliziter Aufruf im Skript nötig.
8. Regulären, port-veröffentlichten Container frisch starten (löst den
   Canary endgültig ab), dessen Bereitschaft ebenfalls abwarten.
9. Canary draining setzen, Gnadenfrist, stoppen/entfernen.

Zu jedem Zeitpunkt bleibt mindestens eine gesunde, aktive Instanz
erreichbar — kein Zeitfenster ganz ohne Bedienung.

## Grenzen: ehrlich dokumentierte Vereinfachungen

- **Gnadenfrist statt generischem "laufende Vorgänge abgeschlossen"-
  Signal**: es gibt keinen service-übergreifenden Mechanismus, um zu
  erkennen, wann eine draining Instanz wirklich keine offenen Vorgänge mehr
  hat (das wäre je Service unterschiedlich — ein offener BPMN-Prozess,
  ein laufender Storage-Schreibvorgang, ein offener Signature-Task, ...).
  Ein fester, konfigurierbarer Zeitraum ist die pragmatische Wahl, die
  Konzept 10.5 selbst für den generellen Fall vorsieht.
- **Consumer-Services ausgeschlossen**: Services mit einem eigenen
  exklusiven NATS-Durable-Consumer (dieselbe Liste wie
  `scripts/run-tests.sh`s `CONSUMER_SERVICES`) können mit diesem Skript
  nicht aktualisiert werden — ein zweiter, gleichzeitig laufender
  Container würde beim Abonnieren mit "consumer is already bound to a
  subscription" fehlschlagen. Ein echter Parallelbetrieb für
  Consumer-Services bräuchte NATS-Queue-Groups statt exklusiver
  Durable-Namen (eine Architekturänderung am Event-Bus-Client, nicht Teil
  dieser Session).
- **Keine echte Container-Automatisierung als Live-Service** (P10-S1-
  Grenze bleibt bestehen): `rolling-update.sh` ist ein von einem
  Menschen/CI ausgeführtes Skript, keine dauerhaft laufende
  Automatisierungskomponente mit Docker-Socket-Zugriff.

## Rollback

Konzept 10.5 verlangt, dass ein Rollback möglich bleibt, "solange der
Drain der alten Instanz noch nicht vollständig abgeschlossen ist". Zwei
Fälle:

- **Canary wird nicht rechtzeitig gesund** (Schritt 4 schlägt fehl): das
  Skript bricht ab, *ohne* die alte(n) Instanz(en) zu draining — nichts
  wurde umgeschaltet, Rollback ist trivial (es gibt nichts zurückzurollen).
- **Nach einem bereits erfolgten Drain stellt sich die neue Version als
  fehlerhaft heraus**: manuell die alte Instanz wieder aktivieren
  (`POST /instances/{old_id}/activate`, seit P10-S3 — Umkehrung von
  `/drain`) und die fehlerhafte neue Instanz ihrerseits draining setzen
  (`POST /instances/{new_id}/drain`) bzw. stoppen. Keine automatische
  Fehlererkennung (bräuchte laufende Health-/Fehlerraten-Beobachtung —
  Monitoring-Territorium, Phase 11).

## Grenzen: Persistenzebene als Sonderfall (Expand/Contract)

Dieses Verfahren funktioniert vollständig unterbrechungsfrei für Updates
ohne Datenbankschema-/Storage-Format-Änderung. Für Schema-Änderungen gilt
das **Expand/Contract-Muster** (auch "Parallel Change" genannt):

- **Expand**: ein additiver Migrationsschritt ergänzt neue Strukturen, ohne
  bestehende zu entfernen — alte und neue Serviceversionen können die
  Datenbank währenddessen parallel nutzen. **Diese Konvention existiert in
  diesem Projekt bereits, hier nur erstmals benannt**: `document-service`
  seit P7-S1 (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS ... DEFAULT ...`),
  `registry-service`s `status`-Spalte und `plugin-orchestration-service`s
  `placement_method`-Spalte (beide P10-S2) sind reale Beispiele — kein
  Alembic in dieser Projektphase (siehe `CONTRIBUTING.md`), `create_all`
  legt nur fehlende *Tabellen* an, ändert aber nie bestehende, daher die
  zusätzliche idempotente `ALTER TABLE`-Zeile im jeweiligen `main.py`.
- **Contract**: ein zweiter, *nachgelagerter* Migrationsschritt entfernt
  die nicht mehr benötigten alten Strukturen, aber **erst, nachdem alle
  Instanzen aktualisiert und die alten vollständig gedraint sind**. Bisher
  hat keine Tabelle in diesem Projekt einen Contract-Schritt gebraucht
  (jede additive Spalte wird bislang weiterverwendet) — wird eine Spalte
  künftig wirklich überflüssig, gehört ihr Entfernen als eigener,
  benannter Schritt in die jeweilige Session, nicht in denselben Schritt
  wie die Erweiterung.
- Für Storage-Format-Änderungen (3.6) gilt sinngemäß dasselbe Prinzip —
  noch nicht praktisch vorgekommen.
- Es wird nicht der Anspruch erhoben, dass *jede* strukturelle Änderung
  unterbrechungsfrei möglich ist — bei einer grundlegenden Restrukturierung
  kann ein kurzes, geplantes Wartungsfenster (analog zum Wartungsmodus bei
  Restore, 10.4) im Einzelfall unumgänglich sein.

## API-Kompatibilität während eines Rollouts

Da alte und neue Serviceversionen für die Dauer des Drains gleichzeitig mit
anderen, noch nicht aktualisierten Services kommunizieren, muss jede
Serviceversion innerhalb eines Rollouts abwärts-/vorwärtskompatibel zur
jeweils anderen bleiben (keine brechenden API-Änderungen innerhalb eines
einzelnen Rollout-Vorgangs) — dieselbe Grunddisziplin, die bereits für die
Versionskompatibilität zwischen föderierten Installationen (7.4)
beschrieben ist, hier auf Service-Ebene innerhalb einer einzelnen
Installation angewendet.
