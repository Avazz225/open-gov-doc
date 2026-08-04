# folder-service

**Verantwortung:** Ordner als hierarchischer Container (Konzept 2.1) — Anlegen, Umbenennen, Verschieben, Löschen (nur wenn leer, oder über den Papierkorb kaskadiert), optionale Objekttyp-Validierung. Besitzt die Ordner-Hierarchie und publiziert Struktur-Events, über die der Permission Service seine Rechte-Vererbung synchron hält. Seit P7-S1b zusätzlich Aufbewahrung/Legal Hold/Zwangslöschung inkl. Löschregister (5.2/5.2a), 1:1 dasselbe Muster wie `document-service` (P7-S1) — kaskadiert dabei auf enthaltene Dokumente.

**Konzept-Referenz:** 2.1, 5.2/5.2a (seit P7-S1b)
**Eigenes Postgres-Schema:** `folder` (Tabellen `folder`, `legal_hold`, `deletion_register_entry`, `retention_config`, `trash_config`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/folders` | Anlegen (`name`, `parent_id` default `"root"`, optional `object_type_id`/`attributes`, `created_by`) |
| `GET` | `/folders/deleted?parent_id=` | Papierkorb-Inhalt eines Ordners (5.2, seit P7-S1b) |
| `GET` | `/folders/{id}` | Metadaten — behandelt einen soft-gelöschten Ordner wie nicht existent (404) |
| `GET` | `/folders/{id}/children` | Direkte Unterordner (nicht gelöschte) |
| `PATCH` | `/folders/{id}` | Umbenennen und/oder verschieben (`parent_id`) und/oder Attribute ändern |
| `DELETE` | `/folders/{id}` | Sofortiger Hard-Delete — 409, falls noch Unterordner vorhanden. Fallback für bereits-leere, nie-retention-behaftete Fälle; der reguläre Weg ist seit P7-S1b `POST .../trash` |
| `POST` | `/folders/{id}/trash` | Papierkorb-Weg (5.2, seit P7-S1b) — kaskadiert über den gesamten aktiven Teilbaum |
| `POST` | `/folders/{id}/restore` | Papierkorb-Wiederherstellung inkl. kaskadierter Unterordner/Dokumente (5.2, seit P7-S1b) |
| `PUT` | `/folders/{id}/retention` | Aufbewahrungsfrist/Zwangslöschung terminieren (5.2/5.2a, seit P7-S1b) |
| `POST` | `/legal-holds` | Legal Hold setzen (5.2, seit P7-S1b) |
| `POST` | `/legal-holds/{id}/release` | Legal Hold aufheben |
| `GET` | `/legal-holds?folder_id=&active_only=` | Legal Holds eines Ordners |
| `GET` | `/deletion-register?folder_id=` | Löschregister (5.2a, seit P7-S1b) |
| `GET`/`PUT` | `/retention-config` | Installationsweite Aufbewahrungs-Grundeinstellungen für Ordner (eigenständig, nicht dieselbe Config wie `document-service`) |
| `GET`/`PUT` | `/trash-config` | Papierkorb-Wiederherstellungsfrist für Ordner (eigenständig) |
| `GET` | `/healthz` | Health-Check |

Ein Wurzelordner (`id: "root"`) wird beim Start idempotent angelegt — analog zum `ROOT_RESOURCE_ID` des Permission Service.

## Datenmodell

`folder`: `id`, `name`, `parent_id` (self-FK, nullable nur für `root`), `object_type_id` (opake Referenz auf Object-Type Service, Integer), `attributes` (JSON), `created_by/at`, `updated_at`. Seit P7-S1b zusätzlich: `deleted_at`, `deleted_via_folder_id` (Kaskaden-Herkunft, s. u.), `retention_until`, `full_deletion`, `pending_deletion_reason`, `deletion_reminder_sent_at`, `reminder_notify_email`, `force_delete_approval_requested_at` — strukturell identisch zu `document_service.Document`s entsprechenden Feldern (P7-S1).

`legal_hold`/`deletion_register_entry`/`retention_config`/`trash_config`: strukturgleich zu den `document-service`-Pendants (siehe dort), aber eigenständige Tabellen mit `folder_id` statt `document_id` — **keine** Wiederverwendung der `document-service`-Tabellen über Service-Grenzen hinweg (kein Cross-Schema-FK, keine verfrühte Zentralisierung in einen Compliance-Service, dieselbe Begründung wie bei P7-S1). Ein Installationsbetreiber kann dadurch für Ordner andere Vorgaben (Wiederherstellungsfrist, Löschgrund-Pflicht) als für Dokumente konfigurieren.

## Objekttyp-Validierung (2.2/4.5)

Trägt ein Folder einen `object_type_id`, wird beim Anlegen `POST /object-types/{id}/validate` des Object-Type Service aufgerufen (Name + Attribute) — schlägt die Validierung fehl, wird der Ordner nicht angelegt (400 mit Fehlerliste). Ohne `object_type_id` entfällt die Prüfung vollständig. Seit P7-S1b zusätzlich: trägt der Objekttyp ein `default_retention_days`, wird beim Anlegen einmalig ein konkretes `retention_until`-Datum übernommen — identisches Muster wie `document-service` (das Feld selbst wurde bereits in P7-S1 objektartübergreifend eingeführt).

**Erzwungene Objekt-Hierarchie (2.2a, seit P5b-S1, ADR 0013)**: derselbe Validierungsaufruf überträgt zusätzlich die Platzierungs-Information des vorgesehenen Elternordners — `parent_is_root: true`, falls `parent_id == "root"`, sonst `parent_object_type_id` (die bereits lokal aus der eigenen `folder`-Tabelle bekannte `object_type_id` des Elternordners, `None` falls dieser untypisiert ist). Object-Type Service löst daraus selbst den Namen der Elternklasse auf und prüft ihn gegen ein eventuelles `allowedParentTypes` des zu platzierenden Typs. Geprüft wird sowohl **beim Anlegen** als auch **beim Verschieben** (`PATCH /folders/{id}` mit geändertem `parent_id`).

## Aufbewahrung, Legal Hold & Zwangslöschung (5.2/5.2a, seit P7-S1b)

Überträgt das in P7-S1 für Dokumente gebaute Muster (siehe `docs/services/document-service.md` für die ausführliche Begründung von Poll-Loop/Legal-Hold/Vier-Augen) auf Ordner — Ordner hatten zuvor **kein einziges** Soft-Delete-Konzept. Zwei Punkte unterscheiden sich substanziell von der Dokument-Variante:

- **Kaskadierender Papierkorb**: `POST /folders/{id}/trash` verschiebt nicht nur den Ordner selbst in den Papierkorb, sondern rekursiv den gesamten **aktiven** Teilbaum (`repository.list_active_subtree_ids`) — Unterordner werden direkt mitmarkiert (`deleted_via_folder_id` = ID des tatsächlich angeklickten Ordners, nicht des jeweiligen direkten Elternordners), enthaltene Dokumente über einen **synchronen** REST-Aufruf an `document-service` (`document_client.py`, `POST /documents/cascade-trash`) — synchron statt eventbasiert, damit z. B. ein sofortiges `GET /documents/deleted` nach dem Löschen bereits konsistent ist. Bereits unabhängig gelöschte Unterordner/Dokumente bleiben unangetastet (kein Überschreiben ihrer Kaskaden-Herkunft). `POST /folders/{id}/restore` spiegelt das exakt: kaskadiert per `deleted_via_folder_id`-Filter zurück, ruft `document_client.cascade_restore` auf — ein unabhängig einzeln gelöschtes Dokument im selben Ordner bleibt dabei im Papierkorb.
- **Keine automatische Kaskaden-Zwangslöschung**: bevor der `_retention_poll_loop` einen Ordner mit `full_deletion=true` physisch entfernt, prüft er über `document_client.count_active()` sowie die eigene Teilbaum-Abfrage, ob noch aktive Unterordner/Dokumente vorhanden sind — falls ja, wird die Zwangslöschung für diesen Tick übersprungen (geloggt, nächster Versuch beim nächsten Tick), **kein** automatisches Mit-Zwangslöschen des Inhalts. Bewusste, konservative Design-Entscheidung: automatisches Ausweiten physischer Löschungen auf einen ganzen Teilbaum wäre ein deutlich größeres Risiko als das bewusst in Kauf genommene "hängt an, bis manuell geleert" (siehe "Offene Punkte").
- **Vier-Augen-Prinzip**: neuer Aktionstyp `folder.force_delete`, exaktes Copy-Paste-Muster von `document.force_delete` (eigener `approval_client.py`/`consumer.py` in diesem Service) — keine Änderung an `permission-service` nötig.
- **Löscherinnerung**: `folder.deletion.reminder`-Event, konsumiert von einem neuen `notification-service`-Consumer (1:1 Kopie des `document.deletion.reminder`-Consumers, nur `name` statt `title` im Payload).
- Storage-Bezug: keiner — Ordner haben keinen eigenen Inhalt, `hard_delete_folder` ist eine reine DB-Zeilen-Entfernung (nach Aufräumen der Legal-Hold-Historie, gleiches Zwischen-Flush-Muster wie `document_service.repository.hard_delete_document`).

## Struktur-Events (Vertrag mit Permission Service)

Publiziert (Stream `folder`, `ensure_stream=True`) exakt den Vertrag, den der Permission Service seit P2-S2 provisorisch erwartet hatte (`docs/services/permission-service.md`):

| event_type | payload |
|---|---|
| `folder.resource.created` | `{resource_id, parent_id, resource_type: "folder"}` |
| `folder.resource.moved` | `{resource_id, new_parent_id}` (nur wenn sich `parent_id` tatsächlich ändert) |
| `folder.resource.deleted` | `{resource_id}` (nur beim direkten Hard-Delete-Fallback, s. o.) |
| `folder.trashed` | `{deleted_by}` (5.2, seit P7-S1b) |
| `folder.restored` | `{}` (5.2, seit P7-S1b) |
| `folder.retention.updated` | `{retention_until, full_deletion}` (5.2/5.2a, seit P7-S1b) |
| `folder.legal_hold.set` / `folder.legal_hold.released` | `{set_by, reason}` / `{released_by}` (5.2, seit P7-S1b) |
| `folder.deletion.reminder` | `{name, retention_until, full_deletion, notify_email}` (5.2a, seit P7-S1b, konsumiert von `notification-service`) |
| `folder.force_deleted` | `{reason, triggered_by}` (5.2a, seit P7-S1b) |
| `folder.trash_purged` | `{trigger: "trash_expiry"}` (5.2a, seit P7-S1b) |

**Konsumiert** (seit P7-S1b, erster Konsument dieses Service überhaupt): `permission.approval.approved` — relevant für `action_type == "folder.force_delete"` (führt eine zuvor per Vier-Augen-Prinzip aufgeschobene Zwangslöschung aus); alle anderen Aktionstypen werden ignoriert.

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`: Register, periodischer Heartbeat, Deregister beim Shutdown) - Grundlage für das Routing des API-Gateways (`docs/services/gateway-service.md`). Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; ohne beide Werte läuft der Service unverändert ohne Discovery.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Tests

**67 Tests** (`test_api.py`, `test_repository.py`, `test_object_type_validation.py`, `test_events.py`, `test_retention.py`, `test_retention_actions.py`, `test_consumer.py`) — die letzten drei Dateien neu seit P7-S1b (Kaskaden-Logik gegen einen Fake-`DocumentClient`, Poll-Loop-Zweige direkt aufgerufen wie bei `document-service`, Vier-Augen-Consumer-Integration, inkl. eines Regressionstests für einen beim Live-Smoke-Test gefundenen echten Bug — siehe `PROGRESS.md`: die Nicht-leer-Prüfung vor einer Zwangslöschung hielt einen Ordner mit nur einem bereits soft-gelöschten Unterordner fälschlich für leer und crashte an der Postgres-FK-Constraint; `has_any_child_folder_row` prüft seither zusätzlich ohne `deleted_at`-Filter).

## Offene Punkte

- **Kein automatisches Kaskadieren der Zwangslöschung auf enthaltenen Teilbaum** (5.2a, seit P7-S1b, siehe oben) — ein Ordner mit noch aktiven Unterordnern/Dokumenten bleibt bei fälliger Zwangslöschung unangetastet, bis der Teilbaum anderweitig (regulär oder per Papierkorb-Ablauf) geleert wurde. Bewusste, konservative Grenze dieses Grundgerüsts, keine bekannte Lücke sondern eine explizite Design-Entscheidung.
- Kein Endpunkt für Breadcrumb/vollständigen Pfad — nur direkte Kinder abrufbar, für die aktuellen Bedürfnisse ausreichend.
- Bereichssperren (4.7, "ganzer Ordnerbereich für reguläre Nutzer gesperrt") sind nicht Teil dieser Session — gehören konzeptionell eher zum Permission Service und sind für eine spätere Phase vorgesehen.
- Kein Rückwirkungs-Check und keine Zyklen-Erkennung für `allowedParentTypes` (siehe ADR 0013) — betrifft dieselbe Einschränkung wie beim Object-Type Service.
- **Keine Legal-Hold-Rollenprüfung** (5.2, seit P7-S1b) — identische offene Frage wie bei `document-service` (P7-S1).
- **Löschregister nicht Backup-differenziert** (5.2a) — identische Einschränkung wie bei `document-service` (Phase 11 fehlt noch). Bei `document-service` wird das teilweise über die `audit-service`-Hash-Kette kompensiert (`document.>` wird dort konsumiert) — `audit-service` konsumiert bislang **kein** `folder.>` (vorbestehende, nicht in dieser Session eingeführte Lücke), daher fehlt diese Kompensation hier vollständig.
