# migration-service

**Verantwortung:** Migration/Transfer Service (Konzept 7.2, P12-S2): Sperren → Kopieren →
Verifizieren → Freigabe im Zielsystem → Löschung im Quellsystem nach Übergangsfrist, zwischen
zwei direkt gepaarten Installationen dieser Software (kein Hub, siehe ADR 0034 — anders als 7.4/
Federation Hub beschreibt 7.2 einen einmaligen, eigenständigen Transfer, keinen laufenden
föderierten Betrieb). Läuft **selbst als auditierbarer, resumable Workflow** über
`workflow-service` (7.2 wörtlich: "nicht als Sonderfall daneben") — jede Instanz dieses Service
kann sowohl Quelle als auch Ziel eines Transfers sein.

**Konzept-Referenz:** 7.2
**Eigenes Postgres-Schema:** `migration` (Tabellen `paired_installation`, `transfer`,
`inbound_transfer`)
**ADR:** [0034 — Direktes Installations-Paar + generische Connector-Service-Tasks](../adr/0034-migration-service-direct-pairing-and-generic-connector-service-tasks.md)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/paired-installations` | Ziel-/Quell-Installation paaren — `api_key` leer lassen, um einen neuen zu generieren (einmalig zurückgegeben), oder den von der Gegenseite bereits ausgegebenen Key eintragen |
| `GET`/`DELETE` | `/paired-installations[/{id}]` | Auflisten (nie mit `api_key`) / Entfernen |
| `POST` | `/transfers` | Transfer starten — Vier-Augen-fähig (4.3, `action_type=migration.transfer.start`), `404` bei unbekanntem Ziel, `dry_run`/`retention_days` optional |
| `GET` | `/transfers[/{id}]` | Status/Liste, optional nach `status` gefiltert |
| `POST` | `/transfers/{id}/steps/{lock\|copy\|verify\|release\|delete-source\|dry-run-check}` | Intern — Ziel der `connector_call`-Service-Tasks in `resources/*.bpmn`, nicht für externe Aufrufer gedacht |
| `POST` | `/inbound/transfers[/...]` | Zielseite — von einer gepaarten Quelle aufgerufen, `Authorization: Bearer <api_key>` |
| `GET` | `/healthz` | Health-Check (ungegated) |

## Ablauf (Quellrolle)

1. **`POST /transfers`** prüft `ApprovalClient.requires_approval("migration.transfer.start")` (4.3,
   P6-S4-Muster) — bei aktivem Vier-Augen wird nur ein `ApprovalRequest` angelegt, der eigentliche
   Start passiert erst, wenn `consumer.py` `permission.approval.approved` konsumiert. Sonst legt
   `_start_transfer()` die `transfer`-Zeile an, **committet sie sofort** (nicht erst am Ende der
   Anfrage — der erste `connector_call`-Schritt feuert synchron zurück auf diesen Service selbst,
   eine neue Transaktion sähe die Zeile sonst noch nicht) und startet eine echte BPMN-Instanz in
   `workflow-service` (`migration_transfer.bpmn` bzw. `migration_dry_run.bpmn`).
2. Jeder Schritt der BPMN-Definition ist ein `bpmn:serviceTask` mit `camunda:properties`
   `taskType=connector_call`, `serviceUrl=.../transfers/{transfer_id}/steps/<phase>` — `workflow-
   service` ruft diese synchron auf und merged die JSON-Antwort in die Prozessdaten (siehe
   `docs/services/workflow-service.md` "Connector-Service-Tasks").
3. **`lock`**: `POST permission-service /scope-locks` auf `source_folder_id` (4.7, `blocks_read
   =false` — 7.2 verlangt nur "keine Schreibzugriffe während der Migration", Lesen bleibt erlaubt).
4. **`copy`**: `POST .../inbound/transfers` auf der Zielseite (legt den Zielordner an, registriert
   `inbound_transfer`), dann rekursiver Baumdurchlauf (`DmsTreeClient.list_children`) — pro Ordner
   werden zuerst dessen Rollenzuweisungen übertragen (`permission-service`s `role-assignments`, per
   Rollenname statt lokaler `role_id`, get-or-create auf der Zielseite), dann Dokumente
   (`.../inbound/transfers/{id}/documents`, Prüfsummen werden für den Verify-Schritt gesammelt),
   dann Unterordner (`.../inbound/transfers/{id}/folders`), rekursiv.
5. **`verify`**: `POST .../inbound/transfers/{id}/verify` mit den gesammelten Ziel-Dokument-IDs —
   die Zielseite meldet ihre eigenen, frisch berechneten Prüfsummen zurück, Abgleich gegen die
   beim Kopieren notierten Werte.
6. **`release`**: `POST .../inbound/transfers/{id}/release` + Aufheben der Bereichssperre.
7. **Löschfrist**: ein `bpmn:intermediateCatchEvent`-Timer wartet `retention_days` Tage
   (Prozessvariable `retention_duration`, z. B. `"P30D"`) — getrieben von `workflow-service`s
   bereits bestehender SLA-Poll-Schleife (P6-S2), keine eigene Scheduler-Infrastruktur nötig.
8. **`delete-source`**: `POST folder-service /folders/{source_folder_id}/trash` — kaskadiert
   automatisch über den gesamten Unterbaum (Ordner + Dokumente, P7-S1b).

## Dry-Run (7.2)

Eigene, kürzere BPMN-Definition (`migration_dry_run.bpmn`, ein einzelner `connector_call`-Schritt
`dry-run-check`) statt eines Gateways im Hauptprozess — keine der übrigen vier Phasen (Sperren/
Kopieren/Verifizieren/Löschen) wird durchlaufen. Prüft aktuell nur Erreichbarkeit und Existenz des
Zielordners (`GET /folders/{id}` auf der Zielseite) — **bewusste Grenze**: keine vollständige
Objekttyp-/Constraint-Kompatibilitätsanalyse, wie 7.2 sie als Beispiel nennt ("passende
Objekttypen vorhanden?").

## Direktes Installations-Paar statt Hub (ADR 0034)

`paired_installation` speichert den API-Key im **Klartext** — anders als `federation-hub-
service`s `Installation` (nur Hash, ADR 0028), da diese Installation den Key sowohl als Quelle
aktiv präsentieren als auch als Ziel verifizieren muss (`hmac.compare_digest`, konstante Zeit).
`POST /paired-installations` generiert bei fehlendem `api_key` einen neuen (einmalig
zurückgegeben, wie bei `federation-hub-service`), oder übernimmt einen von der Gegenseite bereits
ausgegebenen Key unverändert — der Admin trägt denselben Key manuell auf beiden Seiten ein.

## `asyncio.to_thread()` für alle DMS-/Peer-Aufrufe (ADR 0034)

`LocalDmsClient`/`PeerClient` sind bewusst synchron (`httpx.Client`, wie `dms-connector-sdk`
selbst, siehe dessen README). Jeder Aufruf läuft über `asyncio.to_thread()` statt direkt in den
`async def`-Endpunkten — ein synchroner HTTP-Aufruf direkt dort würde den gesamten Event-Loop-
Thread blockieren. Beim Selbst-Loopback (siehe unten) führt das zu einem **echten Deadlock**: der
blockierende Aufruf wartet auf eine Antwort von genau dem Thread, den er selbst blockiert — real
aufgetreten (`httpx.ReadTimeout`) und über `asyncio.to_thread()` behoben.

## Bewusste Grenzen

- **Selbst-Loopback statt echter Zwei-Installationen-Test**: ein zweiter unabhängiger Stack ist im
  Sandbox nicht sinnvoll aufsetzbar — gleiche, bereits bei `federation-hub-service` (P6-S9)
  etablierte Konvention.
- **Keine historischen Zeitstempel für migrierte Versionen** — `document-service`s Checkin setzt
  `created_at`/`created_by` serverseitig, migrierte Versionen tragen den Migrationszeitpunkt.
- **`principal_id` bleibt opak** bei kopierten Berechtigungen — kein Identitätsabgleich zwischen
  den Nutzerpopulationen zweier Installationen, funktioniert korrekt bei geteilter Nutzerbasis.
- **Nur die aktuelle Dokumentversion wird migriert**, nicht die volle Versionshistorie.
- **Migrierte Ordner landen immer im Wurzelverzeichnis** der Ziel-Installation — keine Auswahl
  eines abweichenden Zielorts in dieser Session.

## Konfiguration

| Variable | Default | Bedeutung |
|---|---|---|
| `DMS_DOCUMENT_SERVICE_BASE_URL` | `http://localhost:8006` | Lokaler document-service |
| `DMS_FOLDER_SERVICE_BASE_URL` | `http://localhost:8008` | Lokaler folder-service |
| `DMS_PERMISSION_SERVICE_BASE_URL` | `http://localhost:8004` | Lokaler permission-service (Sperren, Rollen) |
| `DMS_WORKFLOW_SERVICE_BASE_URL` | `http://localhost:8014` | workflow-service (BPMN-Orchestrierung) |
| `DMS_DEFAULT_RETENTION_DAYS` | `30` | Default-Übergangsfrist, falls `POST /transfers` keine explizite angibt |
| `MIGRATION_SERVICE_PORT` | `8028` | Host-Port im Dev-Compose-Stack |

## Lizenzierung

Konzept 9.1 nennt "Migration-Service" wörtlich als Beispiel für eine separat lizenzierbare
Komponente — `registry-service.licensable_components["migration-service"] = "demo"`, gleiches
`LicenseStatusClient`-Muster wie `workflow-service`/`webdav-connector`.

## Tests

Läuft wie `webdav-connector` gegen den echten, laufenden Container (kein In-Prozess-`TestClient`
— der Selbst-Loopback-Smoke-Test braucht einen von aussen über einen echten Netzwerk-Socket
erreichbaren Server, siehe ADR 0034/"Bewusste Grenzen"). `test_full_transfer_lifecycle_self_loopback`
deckt den kompletten Ablauf inkl. Löschung nach Ablauf einer `retention_days=0`-Frist ab.
