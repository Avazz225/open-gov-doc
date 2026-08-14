# storage-service

**Verantwortung:** Storage-Abstraktionsschicht über austauschbare Backend-Plugins — Dateiinhalte liegen in einem oder mehreren konfigurierten Backends (Redundanz, seit P3-S4; beliebig viele, auch gleichartige Instanzen, seit P5b-S6), die Shared DB hält nur Metadaten (Referenz, Prüfsumme, Größe, Kopien-Status, Datenträger-Identität) (Konzept 3.6).

**Konzept-Referenz:** 3.6, 1a (Azure-Blob-Backend, seit Post-Roadmap Phase 24 Session 1), 5.1/5.2a (Object-Lock/WORM, seit P7-S1), 5.6 (Archiv-Zielrolle, seit P7-S3)
**Eigenes Postgres-Schema:** `storage` (Tabellen `object_metadata`, `object_copy`, `backend_identity`, `guard_config`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `PUT` | `/objects/{key:path}?retain_until=...` | Hochladen, berechnet SHA-256, schreibt gemäß Schreibstrategie auf die konfigurierten Ziele, upserted Metadaten — `retain_until` (optional, seit P7-S1) setzt `ObjectCopy.retention_until` und aktiviert auf Zielen mit `object_lock_mode=governance` echtes S3-Object-Lock (siehe unten) |
| `GET` | `/objects/{key:path}` | Herunterladen - liest von der ersten Kopie mit Status `ok` in Zielpriorität, automatischer Fallback (404, wenn keine Kopie verfügbar) |
| `DELETE` | `/objects/{key:path}?bypass_governance=false` | Löschen auf allen Zielen (idempotent), dann Metadaten + Kopien-Einträge — `403`, wenn eine gesperrte Kopie (`retention_until` in der Zukunft, Ziel im Governance-Mode) ohne gültigen Bypass betroffen ist. Bypass erfordert `bypass_governance=true` **und** eine Rolle aus `Settings.governance_bypass_role` im `X-DMS-Roles`-Header (seit P7-S1, [ADR 0030](../adr/0030-storage-object-lock-governance-mode.md)) |
| `GET` | `/object-metadata/{key:path}` | Metadaten lesen |
| `GET` | `/objects/{key:path}/copies` | Kopien-Status je konfiguriertem Ziel (`pending`/`ok`/`failed`/`failed_permanent`) |
| `GET` | `/object-verify/{key:path}` | Fixity-Check des Primärziels: Prüfsumme neu lesen, mit Referenzwert vergleichen |
| `GET` | `/object-verify/{key:path}/all` | Fixity-Check über **alle** konfigurierten Ziele, aktualisiert `object_copy` |
| `GET` | `/storage/usage` | Aggregierter Speicherverbrauch je Backend (`{backend, object_count, total_size_bytes}[]`, `GROUP BY backend` über `object_metadata`, seit P7-S2b) — einziger Konsument bislang: der Speicherverbrauch-Bericht des `reporting-service` (siehe `docs/services/reporting-service.md`), Live-Abfrage statt eigenem Read-Modell |
| `POST` | `/replication/process-pending` | Retry-Queue verarbeiten - repliziert ausstehende Kopien nach, für periodischen externen Aufruf gedacht |
| `GET` | `/guard-config` | Aktuelle Wächter-Konfiguration (`allow_degraded_start`) — legt beim ersten Aufruf die Default-Zeile an (P5b-S6) |
| `PUT` | `/guard-config` | Aktualisiert `allow_degraded_start` — wirkt erst beim **nächsten** Start, nicht auf die laufende Instanz |
| `GET` | `/operational-config` | Aktuelle Betriebsparameter (`write_strategy`, `quorum_count`, `max_replication_attempts`, seit **Post-Roadmap Phase 22 Session 6**, [ADR 0091](../adr/0091-connector-operational-config-live-editable.md)) — legt beim ersten Aufruf die Default-Zeile aus den bisherigen Env-Var-Werten an |
| `PUT` | `/operational-config` | Aktualisiert die Betriebsparameter — wirkt **ohne Neustart**, `422` falls `write_strategy=quorum` mit dem gewählten `quorum_count` gegen die (strukturell fest bleibende) Zielanzahl nicht erfüllbar ist |
| `GET` | `/guard-status` | Je konfiguriertem Ziel: zuletzt bestätigte Geräte-ID, Zeitpunkt, Anzahl noch nicht replizierter Kopien (Admin-UI-Statusblock) |
| `POST` | `/guard-status/{target_id}/reidentify` | Akzeptiert einen beabsichtigten Datenträger-Wechsel zur Laufzeit (kein Neustart nötig), P5c-S2 |
| `PUT` | `/guard-status/{target_id}/config` | Ziel-Metadaten live editieren (`object_lock_mode`, `role`, seit **Post-Roadmap Phase 22 Session 7**, [ADR 0092](../adr/0092-storage-target-metadata-editable.md)) — `404` bei unbekanntem Ziel, `422` falls die Änderung kein reguläres Ziel mehr übrig ließe. Wirkt ohne Neustart |
| `PUT` | `/objects/{key:path}/archive-copy` | Schreibt **nur** auf die konfigurierten Archiv-Ziele (`role="archive"`, 5.6, seit P7-S3) — `503` ohne konfiguriertes Archiv-Ziel |
| `GET` | `/objects/{key:path}/archive-copy` | Liest ausschließlich von Archiv-Zielen (Rückholung, seit P7-S3) — unabhängig vom Live-Zustand desselben Schlüssels |
| `GET` | `/objects/{key:path}/archive-copy/verify` | Fixity-Check der Archiv-Kopie, gefiltert auf Archiv-Ziele (seit P7-S3) |
| `DELETE` | `/objects/{key:path}/live-copies` | "Dehydrieren" (5.6, seit P7-S3) — entfernt Kopien nur von den regulären Live-Zielen, Archiv-Kopie bleibt unberührt. Gleiches Governance-Lock-Gate wie die reguläre Löschung |
| `GET` | `/healthz` | Health-Check inkl. aktiven Zielen und Schreibstrategie |

## Backend-Plugin-Interface (3.6)

`StorageBackend` (ABC): `write(key, data, *, lock_until=None)`, `read`, `delete(key, *, bypass_governance=False)`, `exists`, `checksum` — `lock_until`/`bypass_governance` seit P7-S1 für Object-Lock/WORM (siehe unten), von `LocalFilesystemBackend` akzeptiert, aber ignoriert (dokumentierte Grenze, kein echtes WORM auf lokalem Storage). Drei Implementierungen:

- **`LocalFilesystemBackend`** — deckt sowohl "lokales Dateisystem" als auch **NFS** ab: In Kubernetes ist der konfigurierte Pfad der Mountpunkt eines PVC, das Backend sieht nur einen Ordner, unabhängig davon, ob NFS oder Block-Storage dahintersteht. Schreibt atomar (temporäre Datei + `os.replace`) statt mit plattformspezifischem `fcntl`-Locking — dessen Semantik ist über NFS-Implementierungen hinweg inkonsistent, während atomares Rename-nach-Schreiben auf NFSv4+ zuverlässig funktioniert und Teilschreib-Korruption bei gleichzeitigen Schreibern auf denselben Key verhindert. Nebenläufige *Bearbeitung* eines Dokuments ist Aufgabe des Document-Service-Lockings (4.2), nicht dieser Schicht.
- **`S3Backend`** — `aioboto3`, Werkseinstellung MinIO, funktioniert identisch gegen jeden S3-kompatiblen Provider.
- **`AzureBlobBackend`** (Post-Roadmap Phase 24 Session 1, Konzept 1a) — `azure-storage-blob` (`azure.storage.blob.aio`), Verbindungsstring-Auth (kein `azure-identity`/AAD — für dieses dev-fokussierte Projekt bewusst nicht die zusätzliche Komplexität), funktioniert identisch gegen echtes Azure Blob Storage und den lokalen Azurite-Emulator (Werkseinstellung für Tests/Dev, analog zu MinIO bei `S3Backend`). `lock_until`/`bypass_governance` sind hier ein **dokumentierter No-Op** (siehe "Object-Lock/WORM" unten) — kein echtes Azure Immutable Blob Storage.

Alle drei sind unabhängig getestet: `LocalFilesystemBackend` gegen echtes Dateisystem (`tmp_path`), `S3Backend` gegen echtes MinIO, `AzureBlobBackend` gegen echtes Azurite (jeweils nicht gemockt).

## Datenmodell

- `object_metadata`: `object_key` (PK), `backend` (Ziel-`id` des Primärziels zum Zeitpunkt der Anlage/letzten Überschreibung — seit P5b-S6 eine Ziel-`id`, kein Backend-*Typ* mehr, siehe unten), `checksum_sha256`, `size_bytes`, `content_type`, `created_at`, `updated_at`.
- `object_copy`: `object_key` + `backend_id` (zusammengesetzter PK, FK auf `object_metadata`), `status` (`pending`/`ok`/`failed`/`failed_permanent`), `checksum_sha256`, `attempts`, `last_error`, `next_retry_at` (nullable, seit **Post-Roadmap Phase 20 Session 6**, [ADR 0082](../adr/0082-storage-service-replication-jitter-retrofit.md) — Full-Jitter-Backoff, siehe unten), `retention_until` (Datum, nullable, seit P7-S1 — siehe "Object-Lock/WORM" unten), `created_at`, `updated_at` — eine Zeile je konfiguriertem Ziel und Objekt.
- `backend_identity` (neu, P5b-S6): `target_id` (PK), `device_id`, `verified_at` — zuletzt bestätigte Geräte-ID je konfiguriertem Ziel, unabhängig vom Ziel selbst gespeichert (siehe "Datenträger-Wechsel-Wächter" unten).
- `target_override` (Post-Roadmap Phase 22 Session 7, [ADR 0092](../adr/0092-storage-target-metadata-editable.md)): `target_id` (PK), `object_lock_mode`, `role`, `updated_at` — sparse (nur Ziele mit tatsächlich gesetztem Override haben eine Zeile), siehe "Ziel-Metadaten" unten.
- `guard_config` (neu, P5b-S6): einzelne Zeile mit fester `id=1`, `allow_degraded_start`, `updated_at` — gleiches Muster wie `ocr_config` (ocr-service, [ADR 0016](../adr/0016-ocr-configurability-compose-profile-and-live-settings.md)).

## Ziel-Set: beliebig viele Backend-Instanzen (3.6, seit P5b-S6)

`Settings.targets` ist eine echte Liste von `BackendTargetConfig`-Einträgen (`id`, `type: "local"|"s3"|"azure"`, plus typspezifische Zugangsdaten) statt der vorherigen festen Zwei-Slot-Struktur (`backend`/`secondary_backend`, [ADR 0004](../adr/0004-storage-redundancy-scope.md)) — **`id`, nicht `type`, ist der eindeutige Schlüssel**, wodurch beliebig viele gleichartige Instanzen im selben Ziel-Set möglich sind (z. B. zwei unabhängige S3-Provider zusätzlich zu einem lokalen/NFS-Ziel). Konfiguriert als JSON-Liste in `DMS_TARGETS` (pydantic-settings dekodiert komplexe Feldtypen nativ aus einer einzelnen Umgebungsvariable, siehe [ADR 0017](../adr/0017-storage-device-identity-guard.md)):

```
DMS_TARGETS='[{"id":"local","type":"local","base_path":"/data/storage"},
  {"id":"s3-eu","type":"s3","endpoint_url":"...","access_key":"...","secret_key":"...","bucket":"...","region":"..."},
  {"id":"azure-eu","type":"azure","connection_string":"...","container":"..."}]'
```

**`type="azure"`** (Post-Roadmap Phase 24 Session 1) erfordert `connection_string` (voller Azure-Storage-Verbindungsstring, Verbindungsstring-Auth statt `azure-identity`/AAD) und `container` (Azure-Pendant zu `bucket`, eigenes Feld statt Wiederverwendung von `bucket`). Werkseinstellung für Tests/Dev ist [Azurite](https://learn.microsoft.com/azure/storage/common/storage-use-azurite) (`infra/docker-compose.yml`, Service `azurite`, Blob-Port 10000) — funktioniert mit demselben, öffentlich dokumentierten Azurite-Dev-Verbindungsstring (fester Account `devstoreaccount1` + fester, publizierter Dev-Account-Key, kein echtes Geheimnis) identisch gegen echtes Azure Blob Storage. Der `azurite`-Container läuft mit dem Flag `--skipApiVersionCheck`, da das fest gepinnte Azurite-Image nicht zwingend jede vom `azure-storage-blob`-SDK gesendete `x-ms-version` kennt und sonst mit `400 InvalidHeaderValue` ablehnt.

Primärziel ist immer der erste Eintrag (bestimmt Schreib-Synchronität bei `primary_async` sowie die Lesepriorität). `replication.py` selbst war laut ADR 0004 bereits vollständig generisch gegenüber beliebigen Ziel-`id`-Strings — die frühere Zwei-Slot-Beschränkung saß ausschließlich in `Settings`/`backends/__init__.py`, dort ist sie mit dieser Session aufgelöst.

**Rebalancing bei neu hinzugefügtem Ziel** (seit **P5c-S2**): eine Ziel-Set-Änderung erfordert ohnehin einen Neustart (`Settings` wird nur beim Start gelesen) — genau dieser Neustart löst über den Erststart-Bootstrap des Datenträger-Wechsel-Wächters (siehe unten) automatisch auch das Rebalancing aus: `repository.seed_pending_copies_for_new_target` legt für jedes bereits existierende Objekt, das noch keine Kopie auf dem neuen Ziel hat, eine `pending`-Zeile an, die dieselbe Retry-Queue (`POST /replication/process-pending`) danach nachzieht. Kein separater Trigger-Mechanismus, keine Admin-UI-Aktion nötig.

## Betriebsparameter (Post-Roadmap Phase 22 Session 6, [ADR 0091](../adr/0091-connector-operational-config-live-editable.md))

`OperationalConfig` (DB-Singleton, `id=1`, gleiches Get-or-create-Muster wie `GuardConfig`) macht
`write_strategy`/`quorum_count`/`max_replication_attempts` über `GET`/`PUT /operational-config`
live-editierbar — bei jedem betroffenen Request frisch aus der DB gelesen (kein `app.state`-Cache),
wirkt also ohne Neustart. Bewusst **nicht** Teil dieser Session: das Ziel-Set selbst (`Settings.targets`,
inkl. `access_key`/`secret_key`) und `object_lock_mode`/`role` je Ziel bleiben env-var-only —
Zugangsdaten hätten eine neue Verschlüsselungs-/Masking-Infrastruktur erfordert, `object_lock_mode`/
`role` sind WORM-/Aussonderungs-relevant (5.1/5.2a/5.6), ein versehentlicher Live-Wechsel hätte
compliance-relevante Konsequenzen. `PUT /operational-config` wiederholt dieselbe Quorum-
Erfüllbarkeits-Prüfung wie der Start (`_validate_settings`) gegen die (strukturell weiterhin feste)
Zielanzahl, `422` bei Nichterfüllbarkeit. Admin-UI: `/storage-operational-config/`.

## Redundanz & Fixity (Konzept 3.6, seit P3-S4)

- **Schreibstrategien**: `quorum` (synchron, Erfolg erst ab `quorum_count` bestätigten Zielen, bei Nichterreichen werden bereits erfolgreiche Teilkopien best-effort zurückgerollt) oder `primary_async` (Werkseinstellung für den allgemeinen Betrieb: nur das Primärziel synchron, weitere Ziele bleiben `pending` und werden über `POST /replication/process-pending` nachgezogen — Retry-Queue mit `max_replication_attempts`, danach `failed_permanent` + Error-Log als Alarmierungs-Ersatz). **Seit Post-Roadmap Phase 22 Session 6** ([ADR 0091](../adr/0091-connector-operational-config-live-editable.md)): `write_strategy`/`quorum_count`/`max_replication_attempts` sind live über `GET`/`PUT /operational-config` editierbar (`Settings.write_strategy` u. a. liefern nur noch den Seed-Wert der ersten Zeile) — siehe "Betriebsparameter" unten. **Seit Post-Roadmap Phase 20 Session 6** ([ADR 0082](../adr/0082-storage-service-replication-jitter-retrofit.md)): ein Fehlschlag setzt zusätzlich per Full-Jitter-Backoff (`libs/dms-retry`, gleiche Formel wie bei den vier anderen Resilienz-Sessions dieser Phase) ein `next_retry_at` — `list_pending_copies` greift eine `failed`-Zeile erst wieder auf, sobald diese Wartezeit abgelaufen ist, statt sie bei jedem `process-pending`-Aufruf bedingungslos erneut zu versuchen.
- **Lese-Fallback**: `GET /objects/{key}` liest von der ersten Kopie mit Status `ok` in Zielpriorität (Primärziel zuerst).
- **Fixity-Check je Kopie**: `GET /object-verify/{key}/all` liest die Prüfsumme aus jedem Backend neu und vergleicht sie mit dem in `object_metadata` hinterlegten Referenzwert — erkennt Bit-Rot/Manipulation je Ziel unabhängig, aktualisiert `object_copy.status`.
- Die orchestrierende Logik (`replication.py`) ist backend-agnostisch (arbeitet mit `dict[str, StorageBackend]` + `list[str]` Zielpriorität) und unabhängig von der FastAPI-App-Singleton-Konfiguration testbar.

## Datenträger-Wechsel-Wächter (3.6, seit P5b-S6, [ADR 0017](../adr/0017-storage-device-identity-guard.md))

Schützt gegen einen versehentlich getauschten/zurückgesetzten Datenträger, der sonst stillschweigend als "leeres, aber gültiges" Ziel akzeptiert würde:

- Jedes Ziel bekommt bei erstmaliger Nutzung eine generierte Geräte-ID, abgelegt als Marker-Objekt unter dem reservierten Key `__dms_storage_identity__` — über das bestehende `StorageBackend.write`/`read`-Interface, keine neue Backend-Methode. Der reservierte Key enthält bewusst keinen Schrägstrich und kollidiert damit nicht mit echten, stets segmentierten Objekt-Keys (`typ/id/...`).
- Der **Referenzwert** liegt zusätzlich (nicht nur im Backend selbst) in `backend_identity` — bei einem tatsächlichen Rückfall auf ein leeres/falsches Medium fehlt die Marker-Datei im Backend gerade, ein reiner Selbstvergleich des Backends wäre daher wirkungslos.
- **Werkseinstellung: Startverweigerung** (`RuntimeError` vor `yield`, gleiches Fail-fast-Muster wie die bestehende `_validate_settings`-Prüfung), sobald ein Ziel nicht mit seinem bekannten Referenzwert übereinstimmt oder nicht erreichbar ist. Ein neu zum Ziel-Set hinzugefügtes Ziel (kein bekannter Referenzwert vorhanden) wird stattdessen automatisch "geprägt" — kein Fehlschlag beim Erststart.
- **Admin-Override** (`GuardConfig.allow_degraded_start`, `GET`/`PUT /guard-config`) erlaubt einen degradierten Start, **sofern mindestens ein Ziel nachweislich unverändert ist** — bewusst eine proaktiv gesetzte Standing-Policy statt eines Notfall-Schalters im Moment der Verweigerung (der Service, der die Freigabe entgegennehmen müsste, liefe ja gerade nicht; Postgres selbst ist von einem defekten Storage-Backend unabhängig, siehe ADR 0017).
- Im degradierten Fall werden alle `object_copy`-Zeilen der betroffenen Ziele automatisch auf `pending` zurückgesetzt (`repository.reset_copies_for_backend`) — die bereits bestehende Retry-Queue (`POST /replication/process-pending`) zieht sie nach, kein neuer Hintergrundtask (ADR 0004 gilt unverändert weiter).
- `GET /guard-status` zeigt je Ziel die zuletzt bestätigte Geräte-ID/Zeitpunkt sowie die Anzahl noch offener Kopien — ein Ziel mit `pending_copies > 0` befindet sich noch in der Wiederherstellung.
- **Korrekturmechanismus für beabsichtigte Datenträger-Wechsel** (seit **P5c-S2**): `POST /guard-status/{target_id}/reidentify` übernimmt eine bereits vorhandene Marker-Datei des neuen Geräts oder prägt (wie beim Erststart-Bootstrap) eine neue, aktualisiert `backend_identity` und setzt alle bisherigen Kopien des Ziels über `reset_copies_for_backend` auf `pending` zurück — funktional dieselbe Wiederherstellung wie beim automatischen degradierten Start, aber explizit vom Admin angestoßen und **ohne Neustart** (Admin-UI: Button "Datenträger-Wechsel akzeptieren" je Zeile in `/storage-guard/`). Ersetzt die zuvor nötige direkte Korrektur in der `backend_identity`-Tabelle.

## Ziel-Metadaten live editierbar (Post-Roadmap Phase 22 Session 7, [ADR 0092](../adr/0092-storage-target-metadata-editable.md))

`PUT /guard-status/{target_id}/config` macht `object_lock_mode`/`role` je bereits konfiguriertem Ziel
live editierbar — bewusst NUR diese beiden Metadatenfelder, NICHT das Ziel-Set selbst (Zugangsdaten/
`id`/`type`/`base_path` bleiben env-var-only, gleiche Begründung wie bei `OperationalConfig`, ADR 0091:
neue Ziele brauchen echte Infrastruktur, kein reiner Konfigurationswert). `404` bei unbekannter
`target_id`. `422` falls die Änderung KEIN reguläres (nicht-archiviertes) Ziel mehr übrig ließe — ohne
diese Prüfung könnte ein `role="archive"`-Override auf dem letzten regulären Ziel jeden folgenden Upload
mit einem `IndexError` abstürzen lassen (`upload_object` verwendet `app.state.targets[0]` als
Primärziel).

`_compute_target_state()` (`main.py`) merged `Settings.targets` mit allen `target_override`-Zeilen
(sparse, nur überschriebene Ziele haben eine Zeile) zu einer effektiven Ziel-Liste — aufgerufen beim
Start UND bei jedem `PUT`, das Ergebnis wird sofort in `app.state.target_configs`/`.targets`/
`.archive_targets`/`.lock_target_ids` zurückgeschrieben. Anders als `OperationalConfig` (P22-S6, bei
jedem betroffenen Request frisch aus der DB gelesen) wird hier bewusst NICHT bei jedem einzelnen
Lesezugriff neu aus der DB gelesen — `object_lock_mode`/`role` werden an zu vielen Stellen im Code
gebraucht (Upload-/Archiv-Routing, Retention-Guard, Lock-Status-Anzeigen), ein `PUT`-Zeitpunkt-Refresh
von `app.state` erreicht dasselbe Live-Reload-Ergebnis mit deutlich kleinerem Diff. **Bekannte Grenze**:
bei mehreren horizontal skalierten Repliken sieht eine Replik ohne eigenen `PUT`/Neustart die Änderung
nicht — für die aktuelle Single-Replik-Realität dieses Projekts unkritisch. Admin-UI: `/storage-guard/`
(zwei neue Checkbox-Spalten statt der bisherigen rein lesenden "Object Lock"-Spalte).

## Archiv-Zielrolle (5.6, seit P7-S3)

Aussonderung/Langzeitarchivierung (siehe `docs/services/archival-service.md`) braucht ein eigenes, ggf. günstigeres/anders redundantes Speicherziel getrennt von den Live-Zielen — statt eines separaten Speichersystems bekommt `BackendTargetConfig` ein neues optionales Feld `role: "archive" | null` (Default `null` = bestehendes Verhalten, normales Replikationsziel):

```
DMS_TARGETS='[{"id":"local","type":"local","base_path":"/data/storage"},
  {"id":"archive","type":"local","base_path":"/data/archive","role":"archive"}]'
```

- **`resolve_targets()`** (reguläre Upload-Replikation) **filtert Archiv-Ziele heraus** — sie sind kein Teil des normalen Schreib-/Lesepfads (`PUT`/`GET /objects/{key}`). **`resolve_archive_targets()`** liefert stattdessen genau die Ziele mit `role="archive"`.
- **Neue, dedizierte Endpunkte** (s. o.) statt einer Sonderfall-Verzweigung in den bestehenden `/objects/{key}`-Routen: `PUT`/`GET .../archive-copy` schreiben/lesen ausschließlich über `app.state.archive_targets`, `.../archive-copy/verify` wiederverwendet dieselbe Fixity-Logik wie `GET /object-verify/{key}/all`, gefiltert auf Archiv-Ziele.
- **`replication.write_to_targets()`/`delete_from_targets()`** (neu, `replication.py`) statt der bestehenden `write_with_redundancy()`/`delete_from_all()`: Archiv-Schreibvorgänge sind bewusst synchrone Einzelvorgänge ohne Primär-/Sekundär-Unterschied oder Schreibstrategie/Quorum-Semantik (kein Teil des Upload-Hot-Path). `delete_from_targets()` entfernt gezielt nur die `object_copy`-Zeilen der angegebenen (Live-)Ziele — anders als `delete_from_all()`, das über `repository.delete_copies_for_key` **alle** Kopien-Zeilen eines Schlüssels entfernen würde und damit beim Dehydrieren versehentlich auch die Archiv-Kopien-Tracking-Zeile gelöscht hätte.
- **Routenreihenfolge-Falle** (beim Bauen dieser Endpunkte tatsächlich aufgetreten, s. u.): Starlette matched Pfad-Routen in Registrierungsreihenfolge, und `{key:path}` ist ein greedy-Converter — `PUT /objects/{key:path}` (generischer Upload) muss **nach** `PUT /objects/{key:path}/archive-copy` registriert sein, sonst fängt die generische Route jeden Aufruf inkl. `.../archive-copy` als Teil des Schlüssels ab. Alle spezifischeren Suffix-Routen (`.../copies`, `.../archive-copy`, `.../archive-copy/verify`, `.../live-copies`) stehen deshalb im Quellcode vor den generischen `PUT`/`GET`/`DELETE /objects/{key:path}`-Routen.
- **Kein Admin-UI-Editor für die Ziel-Rolle** — wie beim übrigen Ziel-Set (s. o.) ist dies Deployment-Konfiguration (`DMS_TARGETS`), nicht Admin-UI-Formular.

## Object-Lock/WORM (5.1/5.2a, seit P7-S1, [ADR 0030](../adr/0030-storage-object-lock-governance-mode.md))

Zweistufiger Schutz gegen vorzeitige Löschung während einer laufenden Aufbewahrungsfrist:

- **Anwendungsschicht-Guard** (`retention_guard.py`, gleiches Muster wie der Datenträger-Wechsel-Wächter): `find_locked_targets` prüft vor jeder Löschung, ob eine `ObjectCopy.retention_until` in der Zukunft liegt UND das betroffene Ziel `object_lock_mode="governance"` gesetzt hat (`BackendTargetConfig.object_lock_mode`, nur `"governance"` als gültiger Wert — Compliance-Mode würde die in 5.2a verlangte Zwangslöschungs-Ausnahme technisch unmöglich machen). Blockierte Löschungen liefern `403`, es sei denn `?bypass_governance=true` **und** eine Rolle aus `Settings.governance_bypass_role` (Default `dms-admin`) sind im `X-DMS-Roles`-Header vorhanden (`has_governance_bypass_role`) — exakt dasselbe Header-Rollenmuster wie `document-service`s `kennzeichen_admin_role`.
- **Echtes S3 Object Lock als zusätzliche Härtung**: für `type="s3"`-Ziele mit gesetztem `object_lock_mode` setzt `write()` bei übergebenem `lock_until` `ObjectLockMode="GOVERNANCE"`/`ObjectLockRetainUntilDate`; `delete()` nutzt bei autorisiertem Bypass `BypassGovernanceRetention=True`. **Kritischer Implementierungspunkt**: auf einem versionierten Bucket (Object Lock erfordert Versionierung) entfernt ein `delete_object()` ohne explizite `VersionId` nur einen Delete-Marker, die gesperrte Version bleibt real bestehen — `delete()` liest deshalb bei `object_lock_enabled` zuerst per `head_object` die aktuelle `VersionId` und übergibt sie explizit an `delete_object`.
- **Kein automatischer Eingriff am bestehenden Bucket**: `ObjectLockEnabledForBucket=True` wird nur beim `create_bucket`-Zweig von `ensure_bucket()` gesetzt — S3 Object Lock lässt sich nicht nachträglich auf einen bereits existierenden Bucket aktivieren. Für einen längst produktiv genutzten Bucket bleibt der `head_bucket`-Erfolgszweig unverändert ein No-Op.
- **`local`-Backend-Typ**: keine echte Object-Lock-Entsprechung, nur die Anwendungsschicht-Prüfung greift — ehrlich dokumentierte Grenze, siehe ADR 0030.
- **`azure`-Backend-Typ** (Post-Roadmap Phase 24 Session 1): ebenfalls keine echte Object-Lock-Entsprechung — `AzureBlobBackend.write()`/`delete()` nehmen `lock_until`/`bypass_governance` entgegen, ignorieren sie aber (dokumentierter No-Op, gleiche Haltung wie beim `local`-Backend). Azure Blob Storage kennt technisch ein Äquivalent (Immutable Blob Storage/Time-Based Retention, versionierungs-/richtlinienbasiert), das wurde hier **bewusst nicht implementiert**: Azurite — die Referenz-Testumgebung dieses Projekts — unterstützt Immutability-Policies bislang nicht, ein gegen Azurite ungetestetes "technisches WORM" wäre ein vorgetäuschter statt ein echter Schutz. Wer echten Manipulationsschutz braucht, muss weiterhin ein `type="s3"`-Ziel mit `object_lock_mode=governance` einsetzen (echtes S3 Object Lock, s. o.); der Anwendungsschicht-Guard (`retention_guard.py`) schützt ein Azure-Ziel unabhängig davon genauso wie jedes andere.

## Events

Keine — Storage Service publiziert/konsumiert weiterhin keine Events.

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`: Register, periodischer Heartbeat, Deregister beim Shutdown) - Grundlage für das Routing des API-Gateways (`docs/services/gateway-service.md`). Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; ohne beide Werte läuft der Service unverändert ohne Discovery. Die Selbst-Registrierung passiert **nach** dem Datenträger-Wechsel-Wächter — ein verweigerter Start meldet den Service also gar nicht erst an (kein "healthy=false"-Registry-Eintrag, sondern schlicht kein Eintrag).

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Tests

- `uv run pytest services/storage-service/tests` (**134 Tests seit Post-Roadmap Phase 24 Session 1**
  (Azure-Blob-Backend, Konzept 1a) — +12 gegenüber vorher 122: `test_azure_backend.py` (10 Tests, gegen
  echtes Azurite, kein Mocking, gleiches Muster wie `test_s3_backend.py`) deckt Schreib-/Lese-Roundtrip,
  Prüfsumme, `exists`, Löschen (inkl. idempotent auf bereits fehlendem Key), `ObjectNotFoundError` bei
  fehlendem Key sowie den dokumentierten No-Op von `lock_until`/`bypass_governance` ab (Löschung wird
  NICHT blockiert, anders als beim `S3Backend` mit `object_lock_enabled`); zwei neue
  `test_backend_factory.py`-Tests (`BackendTargetConfig` verlangt `connection_string`/`container` für
  `type=azure`, `build_backend()` liefert `AzureBlobBackend`). Azurite läuft als neuer
  `infra/docker-compose.yml`-Service (`azurite`, Blob-Port 10000, `--skipApiVersionCheck` gegen
  SDK/Emulator-Versionsdrift), `TEST_AZURE_CONNECTION_STRING` env-überschreibbar wie
  `TEST_S3_ENDPOINT_URL` u. a., Default ist der öffentlich dokumentierte, feste Azurite-Dev-
  Verbindungsstring. Davor 122 Tests seit Post-Roadmap Phase 22 Session 7
  ([ADR 0092](../adr/0092-storage-target-metadata-editable.md)), vorher 117, +5: `PUT .../config` auf
  unbekanntes Ziel → `404`, auf das einzige konfigurierte reguläre Testziel mit `role=archive` → `422`
  ("kein reguläres Ziel mehr übrig"), ein Ende-zu-Ende-Test lädt ein Objekt mit `retain_until` hoch,
  aktiviert danach `object_lock_mode=governance` live und bestätigt eine sofort blockierte Löschung ohne
  Neustart zwischen Upload und Sperr-Aktivierung, plus zwei Repository-Unit-Tests für
  `upsert_target_override`/`list_target_overrides`. `tests/conftest.py`s Teardown-Liste um
  `operational_config`/`target_override` ergänzt (fehlte dort, echter Fund dieser Session); davor 117
  Tests, 4 neu seit **Post-Roadmap Phase 22 Session 6**
  ([ADR 0091](../adr/0091-connector-operational-config-live-editable.md)), vorher 113, +4:
  `GET /operational-config` liefert die Env-Var-Defaults vor dem ersten `PUT`, `PUT` aktualisiert und
  persistiert, `PUT` mit unerfüllbarem `quorum_count` liefert `422`, ein echter Upload direkt nach einem
  `PUT` auf `strategy=quorum` beweist den Live-Reload ohne Neustart — die drei mutierenden Tests nutzen
  eine neue `operational_config_client`-Fixture, die die Env-Var-Defaults nach jedem Test
  wiederherstellt, da dieser Service anders als `permission-service`/`signature-service` keine
  Tabellen-Truncate-Fixture zwischen Tests hat), davor 113 Tests, 4 neu seit **Post-Roadmap Phase 20 Session 6** ([ADR 0082](../adr/0082-storage-service-replication-jitter-retrofit.md)): ein Fehlschlag setzt `next_retry_at` und verhindert ein sofortiges erneutes Aufgreifen, nach Vorspulen des Zeitstempels wird die Zeile wieder aufgegriffen, `list_pending_copies` filtert eine noch nicht fällige Zeile aus, der bestehende Erschöpfungstest wurde auf das neue Backoff-Verhalten angepasst): Backend-Plugins, Fabrikfunktionen, Replikation, Datenträger-Wechsel-Wächter, Object-Lock/WORM unverändert. Neu seit P7-S1: `retention_guard`-Unit-Tests (blockiert/nicht blockiert/Bypass mit/ohne Rolle), `S3Backend`-Object-Lock-Tests gegen echtes MinIO (inkl. des Delete-Marker-vs-echte-Versionslöschung-Falls, siehe oben), `LocalFilesystemBackend` ignoriert die neuen Parameter klaglos, API-Tests für `403` ohne Bypass/`200` mit gültigem Bypass. Neu seit P7-S3: `resolve_targets()` schließt `role="archive"` aus/`resolve_archive_targets()` findet sie (`test_backend_factory.py`), `write_to_targets()`/`delete_from_targets()` gegen echte `LocalFilesystemBackend`-Instanzen (`test_replication.py`), API-Roundtrip Archiv-Kopie hochladen/verifizieren/herunterladen sowie Dehydrieren-lässt-Archiv-Kopie-unberührt über einen neuen `archive_client`-Fixture (mutiert `app.state.backends`/`app.state.archive_targets` nach dem Start, gleiches Muster wie `governance_client`).
- **Live-Verifikation ohne Mocking**: ein echter Datenträger-Wechsel wurde gegen den laufenden Container simuliert (Identitätsdatei manuell verändert, Neustart erzwungen) — Startverweigerung, Admin-Override + degradierter Start, automatische Nachreplikations-Vormerkung und `POST /replication/process-pending` alle 1:1 wie vorgesehen; siehe `PROGRESS.md` für den Ablauf. **Seit P5c-S2 zusätzlich verifiziert**: ein zur Laufzeit hinzugefügtes zweites Ziel bekam beim Neustart automatisch `pending`-Kopien für ein zuvor hochgeladenes Objekt (Rebalancing), und `POST /guard-status/{target_id}/reidentify` hat einen simulierten Datenträger-Wechsel ohne Neustart korrigiert. **Seit P7-S1 zusätzlich verifiziert**: ein rein testweises Zweit-Ziel (frischer MinIO-Bucket, `object_lock_mode=governance`) hat eine Löschung ohne Bypass mit `403` abgelehnt und mit gültigem Bypass tatsächlich (nicht nur per Delete-Marker) gelöscht — der echte, produktiv genutzte Bucket blieb unangetastet. **Seit Post-Roadmap Phase 24 Session 1 zusätzlich verifiziert**: ein rein testweises `type=azure`-Ziel (frischer Azurite-Container, kurzzeitig als Primärziel konfiguriert) hat einen echten Upload/Download/Fixity-Check/Löschung über die reguläre `PUT`/`GET`/`DELETE /objects/{key}`-API durchlaufen (`backend: "azure-test"` in der Metadaten-Antwort, `GET .../copies` zeigte `status: "ok"` für das Azure-Ziel) — der Testcontainer und alle dabei entstandenen `object_copy`-Zeilen wurden danach vollständig aufgeräumt, das Standard-Ziel-Set unverändert wiederhergestellt.

## Offene Punkte

- **Konfiguration je Objekttyp/Ordner statt service-weit** — das Konzept erlaubt Overrides der Schreibstrategie pro Objekttyp/Ordner; dafür fehlt aktuell die Verbindung zwischen Object-Type/Folder Service und Storage Service.
- **`/replication/process-pending` wird seit P26-S4 automatisch periodisch ausgeführt** — `infra/k8s/dms/templates/storage-cronjob.yaml` (siehe [ADR 0101](../adr/0101-storage-cronjob-single-job-no-bulk-verify.md)) ruft den Endpunkt alle 15 Minuten (konfigurierbar, `storageCronJob.replication.schedule`) über einen k8s-`CronJob` auf, sobald über dieses Helm-Chart betrieben wird (kein Äquivalent für den `docker-compose.yml`-Dev-Betrieb — dort bleibt der Endpunkt weiterhin manuell/On-Demand). Nach einem degradierten Start oder einem `reidentify`-Aufruf löst sich `pending_copies > 0` damit spätestens beim nächsten CronJob-Lauf von selbst auf, statt beliebig lange zu "hängen".
- **`/object-verify/{key:path}/all` bleibt weiterhin ein reiner On-Demand-Endpunkt ohne automatische periodische Ausführung** — anders als bei der Replikations-Retry-Queue verifiziert dieser Endpunkt IMMER nur ein einzelnes, per Pfad-Parameter übergebenes Objekt (alle *Ziele* dieses einen Objekts, nicht alle Objekte des Stores); `storage-service` hat keinen Endpunkt, der Objektschlüssel auflistet oder eine Charge noch nicht verifizierter Objekte liefert (kein Fixity-Pendant zu `list_pending_copies`/`process_pending`). Ein P26-S4-CronJob dafür wurde deshalb bewusst NICHT gebaut (siehe [ADR 0101](../adr/0101-storage-cronjob-single-job-no-bulk-verify.md) für die Begründung und einen Gestaltungsvorschlag für einen künftigen Bulk-Verify-Endpunkt analog zur Retry-Queue).
- **Kein Entfernen eines Ziels aus dem Ziel-Set** — ein einmal konfiguriertes Ziel lässt sich aktuell nicht sauber "stilllegen" (die zugehörigen `object_copy`-Zeilen blieben verwaist); nur das *Hinzufügen* wurde mit P5c-S2 adressiert. **Bei der Live-Verifikation dieser Session (P24-S1) aus genau diesem Grund manuell per SQL bereinigt** (30.410 durch das Rebalancing beim Hinzufügen des Testziels seedende `pending`-Zeilen für alle bereits existierenden Objekte) — ein konkreter, praktisch erlebter Beleg für diese bereits dokumentierte Lücke.
- **`local`-Backend ohne echtes WORM** (nur Anwendungsschicht-Guard, siehe ADR 0030) — wer manipulationssicheres WORM auf lokalem Storage braucht, muss ein S3-kompatibles Ziel mit `object_lock_mode=governance` einsetzen.
- **`azure`-Backend ohne echtes WORM** (Post-Roadmap Phase 24 Session 1, nur Anwendungsschicht-Guard, siehe "Object-Lock/WORM" oben) — Azure Immutable Blob Storage wäre technisch möglich, wurde aber bewusst nicht implementiert, da Azurite (Referenz-Testumgebung) es nicht unterstützt; wer echtes WORM braucht, muss weiterhin ein `type="s3"`-Ziel mit `object_lock_mode=governance` einsetzen.
- **Azurite-Emulator-Versionsdrift**: das fest gepinnte `azurite`-Image (`3.30.0`) kennt nicht zwingend die vom jeweils aktuellen `azure-storage-blob`-SDK gesendete `x-ms-version` — abgefangen über das Azurite-CLI-Flag `--skipApiVersionCheck` (siehe `infra/docker-compose.yml`); bei einem SDK-Versionssprung mit tatsächlich inkompatiblen (nicht nur unbekannten) Request-Feldern würde dieses Flag nicht mehr helfen und Azurite müsste aktualisiert werden.
- **`replication.py`s `process_pending` propagiert `retention_until` an `record_copy`, aber nicht `lock_until` an den Backend-`write()`-Aufruf bei nachgeholter Replikation** — relevant erst, sobald Nachreplikation regelmäßig für Governance-Ziele genutzt wird (dokumentiert in ADR 0030).
- **Kein automatisches Bucket-Upgrade** für bereits produktiv genutzte Buckets ohne Object Lock (siehe ADR 0030) — nur neu angelegte Buckets erhalten `ObjectLockEnabledForBucket=True`.
- **`PUT /guard-status/{id}/config`s Live-Reload (Post-Roadmap Phase 22 Session 7, ADR 0092) wirkt nur auf die eigene Prozessinstanz** — bei mehreren horizontal skalierten `storage-service`-Repliken sieht eine Replik ohne eigenen `PUT`-Aufruf/Neustart die Änderung nicht (kein geteilter Cache/Pub-Sub-Invalidierung). Für die aktuelle Single-Replik-Deployment-Realität unkritisch.
- **`PUT /guard-status/{id}/config` validiert NICHT nach, ob eine `role`-Änderung den bereits über `PUT /operational-config` gesetzten `quorum_count` unerfüllbar macht** (ADR 0092) — nur die Prüfung "mindestens ein reguläres Ziel bleibt übrig" ist implementiert, die feinere Quorum-Konsistenzprüfung nicht.
