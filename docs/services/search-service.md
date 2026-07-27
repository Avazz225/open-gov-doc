# search-service

**Verantwortung:** Volltextindex + Facettensuche über Dokumentmetadaten und -inhalt (Konzept 3.7) — indexiert Dokumente aus Metadaten-Events, reichert sie nach mit OCR-/Rendering-Volltext an, sobald verfügbar, und filtert Ergebnisse nach den tatsächlichen Leserechten des suchenden Principals.

**Konzept-Referenz:** 3.7
**Eigenes Postgres-Schema:** `search` (`search_document`).

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/search?q=...&folder_id=...&object_type_id=...&created_by=...&created_after=...&created_before=...&attr.{name}[.gte\|.lte]=...&limit=&offset=&sort=` | Suche + Facettenfilter. `q` optional (leer = reine Facetten-Navigation). Erfordert den vom Gateway injizierten `X-DMS-Principal`-Header — fehlt er, `401`. |
| `GET` | `/search/facets` | Verfügbare Objekttypen inkl. Attributschema (für die Filter-UI) |
| `GET` | `/healthz` | Health-Check |

`attr.*`-Filter setzen `object_type_id` voraus (der Attributtyp ist nur über das Objekttyp-Schema bekannt) — ohne `object_type_id` liefert ein `attr.*`-Parameter `400`.

## Datenmodell

`search_document`: ein Eintrag je **Dokument** (natürlicher Schlüssel `document_id`, nicht je Version — Suche bildet den aktuellen Stand ab, nicht die Historie). Felder: `title`, `folder_id`/`folder_name` (denormalisiert), `object_type_id`, `attributes` (`postgresql.JSONB` — bewusst nicht das generische `JSON`, das document-service/ocr-service nutzen, da JSONB die für Attributfilter nötigen `->>`-Operationen sauber unterstützt), `current_version_number`, `full_text`, `created_by`/`created_at`/`updated_at`, `indexed_at`, `search_vector` (`TSVECTOR`, GIN-indexiert). Soft-gelöschte Dokumente werden **hart aus dem Index entfernt** (`DELETE`), nicht nur markiert — es gibt keine UX-Anforderung, gelöschte Treffer anzuzeigen.

`search_vector` wird in `repository.upsert_document()` per Roh-SQL-`UPDATE` nach jedem Flush berechnet (`setweight(to_tsvector('german', title), 'A') || setweight(to_tsvector('german', full_text), 'B')`, Titeltreffer werden höher gewichtet) — bewusst keine generierte Postgres-Spalte, damit die Gewichtungslogik hier sichtbar/testbar bleibt statt in der DDL versteckt zu sein.

## Indexierungs-Pipeline

Konsumiert **drei** NATS-Subject-Gruppen über denselben `event_bus`-Client, aber zwei getrennte Durable-Consumer (kein Publisher — Search Service veröffentlicht keine eigenen Events, dieselbe reine Konsumentenrolle wie audit-service):

- **`document.>`** (Durable `search-service`): `document.deleted` löscht die Indexzeile; `document.created`/`document.version.created`/`document.metadata.updated` lösen `reindex_document()` aus.
- **`ocr.>` + `rendering.>`** (Durable `search-service-text`): `ocr.completed` (Status `ready`/`needs_review`) und `rendering.completed` (nur `rendition_type=="substitute_text"`, Status `ready`) lösen ebenfalls `reindex_document()` aus.

**Dokument-Events sind bewusst dünn** (`document.created` liefert nur `{title, created_by}`, `document.version.created` nur `{version_number, is_conflict, created_by}`, `document.metadata.updated` nur `{title}`) — `reindex_document()` lädt bei jedem Aufruf den vollen Datensatz per `GET /documents/{id}` nach, unabhängig davon, welches Event den Aufruf ausgelöst hat.

**Cross-Stream-Backfill-Race, bewusst durch einen einzigen Codepfad gelöst**: `document.>` und `ocr.>`/`rendering.>` sind getrennte JetStream-Streams ohne Reihenfolgegarantie zueinander — beim allerersten Start eines frischen Search-Service-Consumers (kein `deliver_new`, holt die komplette Historie nach) kann ein `ocr.completed` für ein Dokument ankommen, bevor dessen `document.created` verarbeitet wurde. Da `reindex_document()` in beiden Fällen den vollen Dokumentzustand per HTTP nachlädt (statt sich auf eine bereits existierende Indexzeile zu verlassen), ist die Ankunftsreihenfolge unerheblich — es gibt nur einen Codepfad für "wie soll die Indexzeile dieses Dokuments gerade aussehen", nicht zwei divergierende ("neu anlegen" vs. "nur Text aktualisieren").

**Volltext-Herkunft**: OCR-Ergebnis (`GET /ocr-results` am OCR Service) wird bevorzugt, `substitute_text`-Rendition (`GET /renditions` am Rendering Service, client-seitig auf `rendition_type=="substitute_text"` gefiltert, da der Endpunkt selbst nicht danach filtert) ist der Fallback, sobald keines von beidem vorliegt. Bei einem Versionswechsel (`current_version_number` weicht vom bereits indexierten Stand ab) wird `full_text` zunächst zurückgesetzt, bis die neue Version ihr eigenes OCR-/Rendering-Ergebnis bekommen hat — kein Übertragen veralteter Inhalte.

## Berechtigungsfilterung (3.1, kritischer Befund dieser Session)

**Dokumente sind selbst keine Permission-Resources.** `permission-service` registriert nur Ordner als `ResourceNode` (`structure_subjects = ["folder.>"]`) — `document-service` ruft `permission-service` an keiner Stelle auf. Ein Suchergebnis wird deshalb über seine **`folder_id`** geprüft, nicht über die `document_id` (Dokumente ohne `folder_id` werden auf die Resource `"root"` abgebildet).

Ablauf in `GET /search`: `principal_id` wird aus dem vom Gateway injizierten `X-DMS-Principal`-Header gelesen (JWT-`sub`, `services/gateway-service/src/gateway_service/main.py`) — **kein** Query-Parameter, um Fälschung durch den Client auszuschließen. Die SQL-Suche läuft zunächst mit einer Überfetch-Marge (`(limit+offset) * search_result_overfetch_factor`, hart begrenzt auf `search_result_hard_limit`), da erst danach per `POST /check/batch` am Permission Service (neu in dieser Session, siehe `docs/services/permission-service.md`) geprüft wird, welche der betroffenen `folder_id`s der Principal lesen darf — nicht lesbare Treffer werden aus der Liste entfernt (kein "gesperrt"-Marker, da es dafür kein bestehendes UI-Muster gibt), erst danach wird paginiert.

## Anbindung an das Backend

- **Document Service** (3.1): `GET /documents/{id}` — voller Metadatenstand nach jedem Event.
- **Folder Service** (3.1): `GET /folders/{id}` — Denormalisierung von `folder_name` (keine Code-Änderung an Folder Service nötig).
- **Object-Type Service** (3.1, reiner Referenzdaten-Dienst ohne eigene Events): `GET /object-types`/`GET /object-types/{id}` — synchron abgefragt für Facetten-Definitionen und Attributfilter-Typauflösung (keine Code-Änderung nötig).
- **Permission Service** (3.1): `POST /check/batch` — Berechtigungsfilterung, siehe oben.
- **OCR Service** (3.9, P5-S3) / **Rendering Service** (3.7, P5-S2): Volltextquellen, siehe Indexierungs-Pipeline oben.

## Warum keine Audit-Erweiterung (bewusst geprüft und verworfen)

Search Service publiziert keine eigenen Events — 5.3 verlangt die Auditierung von Dokumentverarbeitungs-Operationen (Upload, Scan, Rendering, OCR), nicht von Lese-/Suchvorgängen. `audit-service` bleibt daher unverändert.

## Selbst-Registrierung (Konzept 3.2a)

Meldet sich beim Start über `dms-registry-client` selbst bei der Registry an — Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Tests

- `uv run pytest services/search-service/tests`: Repository (Upsert/Löschen, `search_vector`-Gewichtung Titel vs. Volltext, Attributfilter je Typ — String exakt, Decimal/Date-Bereich —, Facetten-Gruppierung), Pipeline (`reindex_document` gegen den echten laufenden Document-/Folder-Service inkl. Ordnernamens-Denormalisierung, Versionswechsel-Reset, gelöschtes/unbekanntes Dokument), Consumer-Integration (echtes NATS-Event löst echte Indizierung aus; expliziter Regressionstest für die Cross-Stream-Backfill-Race — ein `ocr.completed`-Event ohne vorher verarbeitetes `document.created` erzeugt trotzdem eine vollständige Indexzeile), API (`/search`/`/search/facets` inkl. echter Berechtigungsfilterung gegen den laufenden Permission Service, `401` ohne Principal-Header).
- **Wichtiger Testbefund dieser Session**: `TestClient(app)`-basierte Tests (API-/Consumer-Integrationstests) verbinden sich über die vom Service selbst gelesene `DMS_POSTGRES_DSN`-Umgebungsvariable — **nicht** über `TEST_POSTGRES_DSN`, das nur die direkt in `conftest.py`/Repository-Tests aufgebauten Engines betrifft. Für eine isolierte Testdatenbank müssen deshalb **beide** Variablen gesetzt werden, sonst schreibt/liest die echte FastAPI-App weiterhin gegen die Live-Datenbank, während die übrigen Tests bereits korrekt isoliert sind (in dieser Session live beobachtet: ein erster Testlauf ohne `DMS_POSTGRES_DSN` erzeugte eine reale, harmlose aber ungewollte Testzeile in der Live-`dms`-Datenbank und ließ einen Assertion auf die isolierte Datenbank fehlschlagen — behoben, Zeile bereinigt, Testlauf mit beiden Variablen wiederholt: grün).
- **Live-E2E über den echten Gateway-Stack**: echtes PDF mit Textlayer aus P5-S3 gefunden über `GET /search?q=Rechnung` inkl. korrektem Ranking/Snippet; Suche ohne `X-DMS-Principal`-Header → `401`; Suche mit einem Principal ohne Ordner-Leserecht liefert das Dokument nicht, nach Anlegen einer Rollenzuweisung auf `"root"` über den echten Permission Service erscheint es; Gateway-Routing erzwingt Auth wie bei allen anderen Services.

## Offene Punkte

- **Deep-Paging unter starker Rechtefilterung ist nicht paginierungsstabil**: die Überfetch-Marge (fester Faktor + harte Obergrenze) kann bei einem Principal mit sehr eingeschränkten Ordnerrechten dazu führen, dass eine späte Seite leerer erscheint, als es tatsächlich verfügbare Treffer gäbe — ein SQL-seitiger Join mit dem Permission Service ist wegen 3.1 (kein Cross-Service-Datenbankzugriff) nicht möglich, eine vollständig stabile Lösung wäre für den aktuellen Umfang Overengineering.
- **Kein `pg_trgm`-Fuzzy-Matching** (siehe ADR 0012) — nur exakte Volltextsuche über Postgres' Stemming-Logik.
- **`attr.*`-Filter erfordern `object_type_id`** — ohne bekannten Objekttyp ist der Attributtyp (Exakt- vs. Bereichsfilter) nicht auflösbar.
- **Ordner-Umbenennung aktualisiert `folder_name` nicht rückwirkend** — erst beim nächsten Re-Index des jeweiligen Dokuments (akzeptierte Inkonsistenz, gleiches Muster wie andere "eventually consistent, erneutes Anfassen aktualisiert"-Fälle in diesem System).
- **Keine weitere Autorisierung außer der Ordner-Leserechtsprüfung** — Search Service ist der erste Service, der den vom Gateway injizierten `X-DMS-Principal`-Header überhaupt auswertet; alle übrigen bisherigen "Offene Punkte" zu fehlender Autorisierung im Gesamtsystem bleiben unverändert bestehen.
