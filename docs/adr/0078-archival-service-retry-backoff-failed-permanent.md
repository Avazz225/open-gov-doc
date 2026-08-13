# 0078 — archival-service: Retry/Backoff, `failed_permanent`, manueller Neustart

**Status:** akzeptiert (Session 2 von 7, siehe Phase 20 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 20 Session 2, betrifft `archival-service`

## Entscheidung

`ArchivalTransfer`/`CaseArchivalTransfer` hatten bislang **gar keinen Retry-Mechanismus**: ein
technischer Fehlschlag in einer Phase setzte sofort `status="failed"` (terminal, außerhalb der
`_ACTIVE_STATUSES`-Menge) — der Transfer verschwand dauerhaft aus dem aktiven Satz, ohne jede
automatische Wiederholung und ohne manuelles Bedienelement, um ihn neu zu starten (bereits als offener
Punkt in `docs/services/archival-service.md` dokumentiert). Diese Session schließt die Lücke mit dem in
P20-S1 (`libs/dms-retry`, [ADR 0077](0077-dms-retry-backoff-jitter-lib.md)) angelegten Backoff-Baustein:

1. **Neue Felder** `attempts: int` (Default 0) und `next_retry_at: datetime | None` auf beiden Modellen.
2. **`mark_failed` verhält sich jetzt retry-bewusst**: ein Fehlschlag unterhalb von
   `Settings.max_archival_attempts` (Default 5, gleicher Zahlenwert wie `storage-service`s
   `max_replication_attempts`) verlässt **nicht mehr sofort** die aktuelle Phase — `status` bleibt z. B.
   `locked`/`copied`, nur `attempts`/`error_message`/`next_retry_at` (per
   `compute_backoff_seconds`) ändern sich. Erst nach Erschöpfen von `max_archival_attempts` wechselt
   `status` auf das neue, echte Terminalstatus `failed_permanent`.
3. **`list_active_transfers`/`list_active_case_transfers`** filtern zusätzlich auf
   `next_retry_at IS NULL OR next_retry_at <= now()` — ein Transfer mit offenem Backoff-Fenster wird
   vor dessen Ablauf übersprungen statt in jedem Poll-Tick erneut sofort zu scheitern.
4. **Neue Endpunkte** `POST /archival-transfers/{id}/retry` und `POST
   /case-archival-transfers/{id}/retry` (beide `archival.write`-gegated, gleiches RBAC-Muster wie seit
   P19-S7): `409` wenn der Transfer nicht `failed_permanent` ist, sonst `reset_for_retry` — setzt
   `status="pending"`, `attempts=0`, `next_retry_at=null`, `error_message=null`.
5. **Neue Spalten in `ArchivalTransferOut`/`CaseArchivalTransferOut`** (`attempts`, `next_retry_at`) —
   Grundlage für die Admin-UI-Sichtbarkeit aus P20-S7.

## Begründung

- **Warum `status` bei einem retry-fähigen Fehlschlag NICHT auf einen separaten `failed`-Zwischenwert
  wechselt**: die bisherige `failed`-Semantik war rein terminal (keine Rückkehr in den aktiven Satz
  vorgesehen). Ein Fehlschlag, der noch Versuche übrig hat, ist konzeptionell kein neuer Zustand, sondern
  derselbe Zwischenschritt mit einem zusätzlichen Fehlerprotokoll — das Belassen von `status` in seiner
  aktuellen Phase bedeutet außerdem, dass `list_active_transfers`s bereits bestehender
  `_ACTIVE_STATUSES`-Filter unverändert funktioniert, keine neue Statuskategorie muss dort ergänzt
  werden.
- **Warum manueller Neustart auf `pending` statt Rekonstruktion der unterbrochenen Phase**: beim
  Übergang auf `failed_permanent` wird die zuletzt erreichte Zwischenphase nicht separat vorgehalten
  (nur ein `status`-Feld je Zeile). Ein Neustart bei `pending` ist sicher und einfach: jede Phase
  (`_advance_locked`/`_advance_copied`/`_advance_verified`) holt ihre Eingaben ohnehin frisch (Rendition
  erneut abfragen, Verifikation erneut anstoßen) — ein kompletter Neudurchlauf ist idempotent, eine
  genaue Wiederaufnahme an der unterbrochenen Stelle hätte ein zusätzliches, nur für diesen Sonderfall
  benötigtes Datenfeld erfordert.
- **Warum `max_archival_attempts` als eigenes Setting statt eines Literalwerts**: gleiches Muster wie
  `storage-service.max_replication_attempts` — ein Installationsbetreiber mit besonders unzuverlässigen
  Archiv-/Rendering-Backends kann die Toleranz erhöhen, ohne Code zu ändern.
- **Warum ein einziges `mark_failed`/`reset_for_retry`-Funktionspaar für BEIDE Transfer-Arten**
  (`ArchivalTransfer` UND `CaseArchivalTransfer`) statt zweier Kopien: der bestehende Code nutzte bereits
  dieselbe `mark_failed`-Funktion für beide Modelle (reine Attributzugriffe, kein `isinstance`-Zweig,
  funktioniert dank identischer Feldnamen für beide) — diese Session behält dieses Duck-Typing-Muster
  bei, statt es aufzubrechen.
- **Warum keine neue `failed`-Statuskategorie mehr in Dokumentation/API als aktiv erreichbar geführt
  wird**: `failed` als eigener, sofort terminaler Zwischenwert entfällt ersatzlos zugunsten des
  differenzierteren Modells (Phase bleibt erhalten + `attempts`/`next_retry_at`, oder `failed_permanent`
  nach Erschöpfung) — eine reine Verhaltensverbesserung, kein Datenverlust (bereits vor dieser Session
  in der DB stehende `"failed"`-Zeilen bleiben unverändert lesbar, `list_transfers(status="failed")`
  funktioniert weiterhin als reiner Filter, nur die Pipeline erzeugt diesen Wert nicht mehr neu).

## Konsequenzen

- **Migration bereits laufender Installationen**: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` im
  Lifespan (gleiches Ad-hoc-Migrationsmuster wie `document-service`s `dehydrated_at`, P19-S11) für beide
  neuen Spalten auf beiden Tabellen — `create_all` legt nur neue Tabellen an, ändert aber keine
  bestehenden.
- **Tests**: `archival-service` 71 (vorher 56, +15: Repository-Ebene — Backoff-Verhalten unterhalb/bei
  Erschöpfung von `max_attempts`, `next_retry_at`-Filterung in `list_active_transfers`, `reset_for_retry`;
  Pipeline-Ebene — Transfer bleibt bei Fehlschlag unterhalb `max_attempts` aktiv, erreicht
  `failed_permanent` bei `max_attempts=1`, gleiches Muster für `case_pipeline`; API-Ebene — beide neuen
  `retry`-Endpunkte inkl. `404`/`409`/`403`-Pfade).
- **Live verifiziert** (Image-Neubau + Neustart, Migration per `\d archival.archival_transfer`
  bestätigt): `404` für unbekannten Transfer, `failed_permanent → retry → pending`-Roundtrip für BEIDE
  Transfer-Arten. Da der Standard-Poll-Intervall (eine Stunde) eine natürliche Fälligkeits-Auslösung in
  einer Live-Sitzung unpraktikabel macht, wurden die Testzeilen direkt in der DB auf `failed_permanent`
  gesetzt, statt eine echte Verifikation live scheitern zu lassen — die eigentliche Backoff-/
  Erschöpfungslogik ist bereits umfassend auf Pipeline-Ebene gegen Fake-Clients getestet (siehe oben).
- **`docs/services/archival-service.md`**: Zustandsmaschinen-Tabellen (beide), API-Tabelle, "Offene
  Punkte" (Retry-Lücke als behoben markiert) aktualisiert.
- **Noch nicht Teil dieser Session**: sichtbare Admin-UI-Oberfläche für `failed_permanent`-Transfers
  samt "Erneut versuchen"-Button (P20-S7) — die Backend-Grundlage (neue Felder, neue Endpunkte) ist
  jetzt vorhanden.
- **Vorbestehende, unabhängige Test-Race bei der Live-Verifikation entdeckt, NICHT behoben**: `TestClient(app)`s
  Lifespan startet den Poll-Task, BEVOR der `client`-Fixture-Rumpf `app.state.document_client` durch ein
  `AsyncMock()` ersetzen kann — trifft der allererste Tick auf eine noch echte `DocumentClient`-Instanz
  (Standard-Basis-URL `http://localhost:8006`), kann er in dieser Sandbox (echter laufender
  document-service auf demselben Host-Port) einen ECHTEN, gerade zur Aussonderung fälligen Dokument
  entdecken und einen Transfer in `dms_test` anlegen. Sichtbar geworden, weil diese Session erstmals über
  `POST /documents/{id}/archive-request` echte, sofort fällige Testdokumente auf dem laufenden Stack
  anlegte (für die Retry-Endpunkt-Live-Verifikation) — nach deren Bereinigung (`PUT
  .../archived`) lief die Suite wieder sauber. Außerhalb des Sessionumfangs, vorbestehend und unabhängig
  von den hier vorgenommenen Code-Änderungen (reproduzierbar auch auf dem Stand vor dieser Session).
