# folder-service

**Verantwortung:** Ordner als hierarchischer Container (Konzept 2.1) — Anlegen, Umbenennen, Verschieben, Löschen (nur wenn leer, oder über den Papierkorb kaskadiert), optionale Objekttyp-Validierung. Besitzt die Ordner-Hierarchie und publiziert Struktur-Events, über die der Permission Service seine Rechte-Vererbung synchron hält. Seit P7-S1b zusätzlich Aufbewahrung/Legal Hold/Zwangslöschung inkl. Löschregister (5.2/5.2a), 1:1 dasselbe Muster wie `document-service` (P7-S1) — kaskadiert dabei auf enthaltene Dokumente.

**Konzept-Referenz:** 2.1, 5.2/5.2a (seit P7-S1b)
**Eigenes Postgres-Schema:** `folder` (Tabellen `folder`, `legal_hold`, `deletion_register_entry`, `retention_config`, `trash_config`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/folders` | Anlegen (`name`, `parent_id` default `"root"`, optional `object_type_id`/`attributes`, `created_by`) |
| `GET` | `/folders/deleted?parent_id=` | Papierkorb-Inhalt eines Ordners (5.2, seit P7-S1b). Alternativ `?scope=personal\|admin` statt `parent_id` (installationsweiter Papierkorb, 2.5, seit P15-S1), siehe "Papierkorb-Familie" unten |
| `GET` | `/folders/{id}` | Metadaten — behandelt einen soft-gelöschten Ordner wie nicht existent (404) |
| `GET` | `/folders/{id}/children` | Direkte Unterordner (nicht gelöschte) |
| `PATCH` | `/folders/{id}` | Umbenennen und/oder verschieben (`parent_id`) und/oder Attribute ändern — `inbox`/`outbox` (2.5, seit P15-S3) lehnen ein gesetztes `name`/`parent_id` mit `409` ab, reine Attribut-Änderungen bleiben erlaubt |
| `DELETE` | `/folders/{id}` | Sofortiger Hard-Delete — 409, falls noch Unterordner vorhanden, oder falls `inbox`/`outbox` (seit P15-S3). Fallback für bereits-leere, nie-retention-behaftete Fälle; der reguläre Weg ist seit P7-S1b `POST .../trash` |
| `POST` | `/folders/{id}/purge` | Manuelle, sofortige endgültige Löschung eines bereits im Papierkorb liegenden Ordners (2.5, seit P15-S1) — `409` wenn nicht im Papierkorb oder Teilbaum nicht leer, `403` ohne Löschadministration-Rolle, siehe "Papierkorb-Familie" unten |
| `POST` | `/folders/{id}/trash` | Papierkorb-Weg (5.2, seit P7-S1b) — kaskadiert über den gesamten aktiven Teilbaum. Seit **P7-S1c** optional per Vier-Augen-Prinzip gegated (Aktionstyp `folder.delete`, Löschantrag-Workflow für reguläre Nutzer) — Response `TrashResult{status: "trashed"\|"pending_approval", folder, approval_request_id}`. `409` für `inbox`/`outbox` (seit P15-S3) |
| `POST` | `/folders/{id}/restore` | Papierkorb-Wiederherstellung inkl. kaskadierter Unterordner/Dokumente (5.2, seit P7-S1b) |
| `PUT` | `/folders/{id}/retention` | Aufbewahrungsfrist/Zwangslöschung terminieren (5.2/5.2a, seit P7-S1b) |
| `POST` | `/legal-holds` | Legal Hold setzen (5.2, seit P7-S1b) — seit **Post-Roadmap Phase 19 Session 10** ([ADR 0075](../adr/0075-legal-hold-rbac.md)) `admin.legal_hold`-gegated |
| `POST` | `/legal-holds/{id}/release` | Legal Hold aufheben — seit **P19-S10** ebenso `admin.legal_hold`-gegated |
| `GET` | `/legal-holds?folder_id=&active_only=` | Legal Holds eines Ordners |
| `GET` | `/deletion-register?folder_id=` | Löschregister (5.2a, seit P7-S1b) |
| `POST` | `/folders/{id}/reconcile-restore-deletion` | Löschabgleich nach Restore (10.4, seit P11-S4) — `X-DMS-Roles: dms-admin`, 1:1 dasselbe Muster wie `document-service` |
| `GET`/`PUT` | `/retention-config` | Installationsweite Aufbewahrungs-Grundeinstellungen für Ordner (eigenständig, nicht dieselbe Config wie `document-service`) |
| `GET`/`PUT` | `/trash-config` | Papierkorb-Wiederherstellungsfrist für Ordner (eigenständig) |
| `POST` | `/folder-templates` | Struktur-Vorlage aus einem Teilbaum erfassen (2.5/7.3, seit **P15-S6**) — `404` bei unbekanntem `source_folder_id`, siehe "Struktur-Vorlagen" unten |
| `GET` | `/folder-templates` | Alle Vorlagen (ohne Struktur, nur Metadaten) |
| `GET` | `/folder-templates/{id}` | Einzelne Vorlage inkl. vollständigem Struktur-Baum — `404` bei unbekannter `id` |
| `DELETE` | `/folder-templates/{id}` | Vorlage löschen (nur die Definition, keine bereits angewendeten Ordner) |
| `POST` | `/folder-templates/{id}/apply` | Vorlage unterhalb `target_parent_id` anwenden — erzeugt echte Ordner, `404` bei unbekannter Vorlage/unbekanntem Ziel |
| `GET` | `/healthz` | Health-Check |

Ein Wurzelordner (`id: "root"`) wird beim Start idempotent angelegt — analog zum `ROOT_RESOURCE_ID` des Permission Service. Seit P15-S3 zusätzlich zwei feste Sonderordner **`inbox`**/**`outbox`** (Posteingang/Postausgang, 2.5/3.3) direkt unter `root`, gleiches Idempotenz-Muster (`repository.ensure_special_folders`). Alle drei sind vor Umbenennen/Verschieben (`PATCH`, 409 bei gesetztem `name`/`parent_id`) und Löschen (`DELETE`/`POST .../trash`, 409) geschützt — ein Sonderbereich "existiert genau einmal je Installation" (2.5). `root` erhielt diesen Schutz erst nachträglich in Post-Roadmap Phase 19 Session 11 ([ADR 0076](../adr/0076-root-folder-mail-regex-dehydration-409.md)).

## Datenmodell

`folder`: `id`, `name`, `parent_id` (self-FK, nullable nur für `root`), `object_type_id` (opake Referenz auf Object-Type Service, Integer), `attributes` (JSON), `created_by/at`, `updated_at`. Seit P7-S1b zusätzlich: `deleted_at`, `deleted_via_folder_id` (Kaskaden-Herkunft, s. u.), `retention_until`, `full_deletion`, `pending_deletion_reason`, `deletion_reminder_sent_at`, `reminder_notify_email`, `force_delete_approval_requested_at` — strukturell identisch zu `document_service.Document`s entsprechenden Feldern (P7-S1). Seit P15-S1 zusätzlich `deleted_by` (Voraussetzung für den persönlichen Papierkorb, 2.5).

`legal_hold`/`deletion_register_entry`/`retention_config`/`trash_config`: strukturgleich zu den `document-service`-Pendants (siehe dort), aber eigenständige Tabellen mit `folder_id` statt `document_id` — **keine** Wiederverwendung der `document-service`-Tabellen über Service-Grenzen hinweg (kein Cross-Schema-FK, keine verfrühte Zentralisierung in einen Compliance-Service, dieselbe Begründung wie bei P7-S1). Ein Installationsbetreiber kann dadurch für Ordner andere Vorgaben (Wiederherstellungsfrist, Löschgrund-Pflicht) als für Dokumente konfigurieren.

## Objekttyp-Validierung (2.2/4.5)

Trägt ein Folder einen `object_type_id`, wird beim Anlegen `POST /object-types/{id}/validate` des Object-Type Service aufgerufen (Name + Attribute) — schlägt die Validierung fehl, wird der Ordner nicht angelegt (400 mit Fehlerliste). Ohne `object_type_id` entfällt die Prüfung vollständig. Seit P7-S1b zusätzlich: trägt der Objekttyp ein `default_retention_days`, wird beim Anlegen einmalig ein konkretes `retention_until`-Datum übernommen — identisches Muster wie `document-service` (das Feld selbst wurde bereits in P7-S1 objektartübergreifend eingeführt).

**Erzwungene Objekt-Hierarchie (2.2a, seit P5b-S1, ADR 0013)**: derselbe Validierungsaufruf überträgt zusätzlich die Platzierungs-Information des vorgesehenen Elternordners — `parent_is_root: true`, falls `parent_id == "root"`, sonst `parent_object_type_id` (die bereits lokal aus der eigenen `folder`-Tabelle bekannte `object_type_id` des Elternordners, `None` falls dieser untypisiert ist). Object-Type Service löst daraus selbst den Namen der Elternklasse auf und prüft ihn gegen ein eventuelles `allowedParentTypes` des zu platzierenden Typs. Geprüft wird sowohl **beim Anlegen** als auch **beim Verschieben** (`PATCH /folders/{id}` mit geändertem `parent_id`).

**Bugfix (P14-S12)**: `PATCH /folders/{id}` validierte bislang nur bei einer tatsächlichen Verschiebung (`is_move`) — eine reine Attribut-/Namensänderung ohne Verschiebung überging die Objekttyp-Validierung vollständig, anders als `document-service`s `update_document` (dort läuft die Validierung bei **jeder** PATCH-Anfrage, sobald ein Objekttyp gesetzt ist). Gefunden bei der Recherche zur Sammelbearbeitung von Metadaten (Konzept §8, ADR 0050) — ohne den Fix hätte eine Bulk-Attributänderung an Ordnern die geforderte Constraint-Prüfung für Ordner faktisch nicht durchgesetzt. Jetzt symmetrisch zu `document-service`: validiert bei jeder Änderung, nicht nur bei Verschiebung; `_validate_against_object_type()`s Platzierungs-Parameter werden nur bei einer tatsächlichen Verschiebung befüllt, sonst leer gelassen.

## Aufbewahrung, Legal Hold & Zwangslöschung (5.2/5.2a, seit P7-S1b)

Überträgt das in P7-S1 für Dokumente gebaute Muster (siehe `docs/services/document-service.md` für die ausführliche Begründung von Poll-Loop/Legal-Hold/Vier-Augen) auf Ordner — Ordner hatten zuvor **kein einziges** Soft-Delete-Konzept. Zwei Punkte unterscheiden sich substanziell von der Dokument-Variante:

- **Kaskadierender Papierkorb**: `POST /folders/{id}/trash` verschiebt nicht nur den Ordner selbst in den Papierkorb, sondern rekursiv den gesamten **aktiven** Teilbaum (`repository.list_active_subtree_ids`) — Unterordner werden direkt mitmarkiert (`deleted_via_folder_id` = ID des tatsächlich angeklickten Ordners, nicht des jeweiligen direkten Elternordners), enthaltene Dokumente über einen **synchronen** REST-Aufruf an `document-service` (`document_client.py`, `POST /documents/cascade-trash`) — synchron statt eventbasiert, damit z. B. ein sofortiges `GET /documents/deleted` nach dem Löschen bereits konsistent ist. Bereits unabhängig gelöschte Unterordner/Dokumente bleiben unangetastet (kein Überschreiben ihrer Kaskaden-Herkunft). `POST /folders/{id}/restore` spiegelt das exakt: kaskadiert per `deleted_via_folder_id`-Filter zurück, ruft `document_client.cascade_restore` auf — ein unabhängig einzeln gelöschtes Dokument im selben Ordner bleibt dabei im Papierkorb.
- **Keine automatische Kaskaden-Zwangslöschung**: bevor der `_retention_poll_loop` einen Ordner mit `full_deletion=true` physisch entfernt, prüft er über `document_client.count_active()` sowie die eigene Teilbaum-Abfrage, ob noch aktive Unterordner/Dokumente vorhanden sind — falls ja, wird die Zwangslöschung für diesen Tick übersprungen (geloggt, nächster Versuch beim nächsten Tick), **kein** automatisches Mit-Zwangslöschen des Inhalts. Bewusste, konservative Design-Entscheidung: automatisches Ausweiten physischer Löschungen auf einen ganzen Teilbaum wäre ein deutlich größeres Risiko als das bewusst in Kauf genommene "hängt an, bis manuell geleert" (siehe "Offene Punkte").
- **Vier-Augen-Prinzip**: neuer Aktionstyp `folder.force_delete`, exaktes Copy-Paste-Muster von `document.force_delete` (eigener `approval_client.py`/`consumer.py` in diesem Service) — keine Änderung an `permission-service` nötig.
- **Löscherinnerung**: `folder.deletion.reminder`-Event, konsumiert von einem neuen `notification-service`-Consumer (1:1 Kopie des `document.deletion.reminder`-Consumers, nur `name` statt `title` im Payload).
- Storage-Bezug: keiner — Ordner haben keinen eigenen Inhalt, `hard_delete_folder` ist eine reine DB-Zeilen-Entfernung (nach Aufräumen der Legal-Hold-Historie, gleiches Zwischen-Flush-Muster wie `document_service.repository.hard_delete_document`).

## Löschantrag-Workflow für reguläre Nutzer (5.2, seit P7-S1c)

Eigener Aktionstyp `folder.delete`, getrennt von `folder.force_delete` — Letzteres bleibt für die retentionsgetriggerte Zwangslöschung, `folder.delete` gatet die manuelle, nutzerausgelöste `POST /folders/{id}/trash` (Gate-Prüfung direkt im Endpunkt, `TrashResult`-Wrapper). Bei Genehmigung führt ein neuer `consumer.py`-Zweig (`_handle_delete_approved`) `repository.soft_delete_folder` aus — identische Kaskade auf Unterordner/Dokumente wie beim direkten Aufruf. Keine neue Selbstgenehmigungs-Logik nötig (`permission-service` verhindert bereits generisch Initiator == Genehmiger). Siehe `docs/services/document-service.md` für die ausführliche Architekturbegründung (identisches Muster) und `docs/services/user-ui.md` für die neue Genehmigungs-Inbox.

## Papierkorb-Familie: persönlicher Papierkorb (2.5, seit P15-S1)

Ordner-Pendant zu `document-service`s gleichnamigem Abschnitt — siehe dort für die vollständige Begründung und [ADR 0051](../adr/0051-papierkorb-familie-classification-via-object-type-scoped-global-endpoints.md). Keine Verschlusssachen-Variante: Konzept 2.5 kennzeichnet ausdrücklich nur Dokumente als Verschlusssache, keine Ordner.

- **`deleted_by` nachgerüstet**: `repository.soft_delete_folder` nahm `deleted_by` bereits als Parameter entgegen, persistierte ihn aber nie (gleiche, bei P15-S0 gefundene Lücke wie bei `document-service`) — jetzt echte Spalte, bei `restore_folder` wieder zurückgesetzt.
- **`scope`-Query-Parameter auf `GET /folders/deleted`** (`personal`/`admin`) — rein additiv, ohne `scope` unverändertes, ordnerbezogenes Verhalten. `scope=personal` filtert auf `deleted_by == X-DMS-Principal` (401 ohne Principal-Header), `scope=admin` verlangt `trash_hard_delete_admin_role` (Setting, Default `"dms-admin"`, 403 sonst).
- **`POST /folders/{id}/purge`**: manuelle, sofortige endgültige Löschung eines bereits im Papierkorb liegenden, leeren Ordners — ruft dieselbe `retention_actions.purge_expired_trash_entry()` auf wie der Poll-Loop (jetzt um `trigger`/`triggered_by` erweitert, `trigger="manual_purge"`). Gleiche Sicherheitsprüfung wie die automatische Zwangslöschung: `has_any_child_folder_row`/`document_client.count_active` müssen leer sein (409 sonst), sonst würde die physische Entfernung an der FK-Constraint scheitern — ein verschachtelter Baum muss deshalb von den Blättern nach oben einzeln geleert werden, kein rekursives Bulk-Purge.
- **`get_folder_any_state`** (neue, öffentliche Repository-Funktion) — Gegenstück zu `get_folder` OHNE Papierkorb-Filter, für den Purge-Endpunkt, der gerade einen bereits gelöschten Ordner ansprechen muss.

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

**Audit-Anbindung (seit P7-S2, echter Nachtrag)**: `audit-service` fehlte `"folder.>"` in seiner konsumierten Subject-Liste seit Einführung dieses Streams in P7-S1b — ein beim P7-S2-Live-Smoke-Test entdeckter Bestandsfehler, nachgeholt inkl. rückwirkendem Backfill der kompletten bisherigen Ordner-Ereignishistorie (siehe `docs/services/audit-service.md`).

## Struktur-Vorlagen (2.5/7.3, seit P15-S6)

Ein Ordner-Teilbaum als benannte, wiederverwendbare Vorlage (z. B. Aktenplan-Rohbau) — letzte Session der Phase 15. Vollständige Architekturbegründung: [ADR 0056](../adr/0056-struktur-vorlagen-folder-service-json-tree-no-attribute-values.md).

- **Direkt in `folder-service` gebaut, nicht auf `config-service`s bestehendem 7.3-Export aufgesetzt** — bereits bei P15-S0 korrigiert (Konzepttext trifft technisch nicht zu, siehe `PROGRESS.md`).
- **`FolderTemplate`-Tabelle mit einem verschachtelten JSON-Baum** (`structure`: `{"name", "object_type_id", "children"}`) statt eigenständiger "toter" `Folder`-Zeilen — `repository.build_template_structure` walkt den aktiven Teilbaum rekursiv über die bereits bestehende `list_children` (schließt soft-gelöschte Unterordner automatisch aus). Kein FK auf `Folder` — eine Vorlage bleibt gültig, auch wenn der Quellordner später verschoben/umbenannt/gelöscht wird.
- **Bewusst NUR Struktur, keine Attributwerte** — ein "Rohbau" wird nach dem Anwenden erst befüllt (Pflichtattribute werden dann ganz regulär bei `PATCH /folders/{id}` geprüft).
- **Anwenden überspringt bewusst die object-type-Validierung** — `repository.apply_template`/`_apply_structure_node` rufen `repository.create_folder` direkt auf (nicht den validierenden `POST /folders`-Endpunkt-Codepfad), erzeugen also Ordner mit leeren `attributes={}` unabhängig davon, ob der zugewiesene Objekttyp Pflichtfelder hat. Eltern-Kind-Objekttyp-Nestingregeln (2.2b) werden dabei ebenfalls nicht geprüft — bewusst dokumentierte Grenze, siehe ADR 0056.
- **`folder.resource.created` wird für jeden neu erzeugten Ordner einzeln publiziert** (main.py, nach dem Commit) — identisches Event/Payload wie bei einer regulären `POST /folders`, damit `permission-service`s `ResourceNode`-Baum synchron bleibt. Live gegen den laufenden Stack verifiziert (`GET /resources/{id}` auf `permission-service` bestätigte beide angewendeten Knoten).
- **Ungegated** (keine Rolle erforderlich) — identisches Muster wie die reguläre `POST /folders`, keine Konzeptvorgabe verlangt hier eine Einschränkung.
- **Frontend nutzt rohe Ordner-ID-Texteingaben** für Quell-/Zielordner statt eines eigens gebauten Baum-Pickers — identisches, bereits etabliertes Muster wie `QuarantinePane`/`PoststellePane` (P15-S2/S3).

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`: Register, periodischer Heartbeat, Deregister beim Shutdown) - Grundlage für das Routing des API-Gateways (`docs/services/gateway-service.md`). Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; ohne beide Werte läuft der Service unverändert ohne Discovery.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Tests

**115 Tests** (vorher 99, 16 neu seit **P15-S6**: `test_folder_templates.py` — Struktur-Erfassung inkl. Ausschluss soft-gelöschter Unterordner, unbekannter Quellordner, Erfassen/Auflisten/Lesen/Löschen-Roundtrip, Anwenden erzeugt den vollständigen Teilbaum unterhalb des Ziels ohne den Quellbaum zu verändern, unbekanntes Ziel; `test_api.py`-Erweiterung für die vier neuen `/folder-templates`-Endpunkte inkl. `404`-Fällen); davor 99, vorher 93, 6 neu seit **P15-S3**: `inbox`/`outbox` existieren + liegen unter `root`, Umbenennen/Verschieben/Hart-Löschen/Trashen von `inbox` je `409`, reine Attribut-Änderung bleibt erlaubt; davor 93, vorher 79, 14 neu seit **P15-S1**: `test_api.py` bekam Tests für `scope`-Sichtbarkeit auf `GET /folders/deleted` (401/403/personal/admin-Filterung), `POST /folders/{id}/purge` (401/403/404/409-nicht-im-Papierkorb/409-verbleibende-Kind-Zeile/204-Erfolg inkl. Löschregister-Eintrag), `test_retention.py` bekam Tests für `deleted_by`-Persistierung/-Rücksetzung/-Filterung) (`test_api.py`, `test_repository.py`, `test_object_type_validation.py`, `test_events.py`, `test_retention.py`, `test_retention_actions.py`, `test_consumer.py`) — die letzten drei Dateien neu seit P7-S1b (Kaskaden-Logik gegen einen Fake-`DocumentClient`, Poll-Loop-Zweige direkt aufgerufen wie bei `document-service`, Vier-Augen-Consumer-Integration, inkl. eines Regressionstests für einen beim Live-Smoke-Test gefundenen echten Bug — siehe `PROGRESS.md`: die Nicht-leer-Prüfung vor einer Zwangslöschung hielt einen Ordner mit nur einem bereits soft-gelöschten Unterordner fälschlich für leer und crashte an der Postgres-FK-Constraint; `has_any_child_folder_row` prüft seither zusätzlich ohne `deleted_at`-Filter).

## Offene Punkte

- **Kein automatisches Kaskadieren der Zwangslöschung auf enthaltenen Teilbaum** (5.2a, seit P7-S1b, siehe oben) — ein Ordner mit noch aktiven Unterordnern/Dokumenten bleibt bei fälliger Zwangslöschung unangetastet, bis der Teilbaum anderweitig (regulär oder per Papierkorb-Ablauf) geleert wurde. Bewusste, konservative Grenze dieses Grundgerüsts, keine bekannte Lücke sondern eine explizite Design-Entscheidung.
- Kein Endpunkt für Breadcrumb/vollständigen Pfad — nur direkte Kinder abrufbar, für die aktuellen Bedürfnisse ausreichend.
- Bereichssperren (4.7, "ganzer Ordnerbereich für reguläre Nutzer gesperrt") sind nicht Teil dieser Session — gehören konzeptionell eher zum Permission Service und sind für eine spätere Phase vorgesehen.
- Kein Rückwirkungs-Check und keine Zyklen-Erkennung für `allowedParentTypes` (siehe ADR 0013) — betrifft dieselbe Einschränkung wie beim Object-Type Service.
- **`PATCH /folders/{id}` (Verschieben) prüft nur "nicht sein eigener direkter Elternordner"** (`repository.update_folder`, `new_parent_id == folder_id`) — **keine tiefere Zyklenprüfung** (z. B. Ordner A in seinen eigenen Enkel verschieben). Gefunden/live bestätigt bei P23-S4 (`user-ui`s neues Drag-&-Drop-Verschieben, `apps/user-ui/src/components/FolderTree.tsx`): ein clientseitiger Schutz verhindert einen sichtbaren Zyklus nur innerhalb des im Baum aktuell geladenen/sichtbaren Teils (Ziel darf kein bereits geladener Nachfahre des gezogenen Ordners sein), verhindert aber **keinen** Zyklus über direkte API-Aufrufe oder unsichtbare Tree-Teile hinweg — ein per curl erzwungener zweigliedriger Zyklus (A→B, dann B→A) wurde bestätigt möglich und macht danach BEIDE Ordner unlöschbar (die Nicht-leer-Prüfung sieht in jedem den jeweils anderen als Kind). Bewusst nicht in P23-S4 behoben (reiner Frontend-Sessionsumfang) — eine echte Fix würde beim Verschieben serverseitig die Vorfahrenkette des neuen Elternordners bis zur Wurzel durchlaufen und ablehnen, falls der zu verschiebende Ordner darin vorkommt.
- ~~Keine Legal-Hold-Rollenprüfung (5.2, seit P7-S1b) — identische offene Frage wie bei `document-service` (P7-S1)~~ — **behoben in Post-Roadmap Phase 19 Session 10** ([ADR 0075](../adr/0075-legal-hold-rbac.md)), gemeinsam mit `document-service`. Erster Konsument von `libs/dms-permission-client` in diesem Service.
- **Löschregister nicht Backup-differenziert** (5.2a) — identische Einschränkung wie bei `document-service` (Phase 11 fehlt noch). Bei `document-service` wird das teilweise über die `audit-service`-Hash-Kette kompensiert (`document.>` wird dort konsumiert) — `audit-service` konsumiert bislang **kein** `folder.>` (vorbestehende, nicht in dieser Session eingeführte Lücke), daher fehlt diese Kompensation hier vollständig.
- **Struktur-Vorlagen prüfen beim Anwenden weder Pflichtattribute noch Eltern-Kind-Objekttyp-Nestingregeln (2.2b)** (seit P15-S6, siehe oben) — bewusste, dokumentierte Vereinfachung (ADR 0056), kein Modus in `object-type-service` für "nur Struktur-, keine Attributprüfung" vorhanden.
- **`created_by` bei den Vorlagen-Endpunkten bleibt client-seitig im Body** (seit P15-S6) — folgt bewusst dem bereits durchgängig in diesem Service verwendeten Muster (`FolderCreate.created_by`, `TrashRequest.deleted_by`), nicht der neueren, an anderer Stelle im Projekt bereits gehärteten `X-DMS-Principal`-Konvention — vorbestehende, nicht in dieser Session behobene Alt-Lücke des gesamten Service.
- ~~**`root` selbst hat keinen Umbenennen-/Verschieben-/Löschen-Schutz** (P15-S3, beim Bauen des neuen `inbox`/`outbox`-Schutzes gefunden)~~ — **behoben in Post-Roadmap Phase 19 Session 11** ([ADR 0076](../adr/0076-root-folder-mail-regex-dehydration-409.md)): `root` ist jetzt Teil von `PROTECTED_FOLDER_IDS`, durchläuft dieselben drei bestehenden `409`-Prüfungen wie `inbox`/`outbox`.
