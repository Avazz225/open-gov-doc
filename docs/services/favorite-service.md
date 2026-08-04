# favorite-service

**Verantwortung:** Favoriten/Merkliste (schnelles Wiederfinden, seit P7-S1d) — persönliche Lesezeichen einzelner Nutzer auf Dokumente und Ordner. Rein nutzerbezogen (`user_id`/`object_type`/`object_id`), kein Bezug zu Aufbewahrung/Löschung/Genehmigungen. *(Nutzeridee bei der P7-S1b-Planfreigabe, zurückgestellt als eigene Session, siehe `PROGRESS.md`.)*

**Konzept-Referenz:** keine (Nutzerwunsch außerhalb des ursprünglichen Konzepts).
**Eigenes Postgres-Schema:** `favorite` (Tabelle `favorite`).

## Architekturentscheidung: bewusst keine referenzielle Prüfung

Anders als z. B. `case-service` (das seine Dokumentreferenzen aktiv gegen `document-service` validiert) prüft `favorite-service` beim Anlegen **nicht**, ob `object_id` tatsächlich existiert. Ein Favorit ist ein niedrigschwelliges persönliches Lesezeichen, keine Geschäftsdatenreferenz mit Nachvollziehbarkeitspflicht — eine verwaiste Referenz (z. B. nach Löschung des Originals) richtet keinen Schaden an. Das Auflösen des Anzeigenamens übernimmt die aufrufende UI (`user-ui`s `FavoritesPane`, siehe `docs/services/user-ui.md`), die einen 404 beim Auflösen toleriert statt die ganze Liste scheitern zu lassen. Dadurch bleibt dieser Service vollständig entkoppelt — keine Cross-Service-HTTP-Clients, kein `depends_on` außer Postgres/NATS.

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/favorites` | Anlegen (`user_id`, `object_type`: `"document"`\|`"folder"`, `object_id`) — `201`, `409` bei bereits bestehendem Favoriten (Unique-Constraint `user_id`+`object_type`+`object_id`) |
| `GET` | `/favorites` | Liste für einen Nutzer (`user_id` Pflicht-Query-Parameter, `object_type` optional filterbar), neueste zuerst |
| `DELETE` | `/favorites` | Entfernen über Query-Parameter (`user_id`, `object_type`, `object_id`) statt Pfad-`id` — der Aufrufer (Kontextmenü) kennt nur das favorisierte Objekt, nicht die interne Favoriten-`id`. `404` falls nicht favorisiert |
| `GET` | `/healthz` | Health-Check |

## Datenmodell

- `favorite`: `id` (UUID), `user_id`, `object_type` (`"document"`\|`"folder"`), `object_id`, `created_at`. Unique-Constraint auf `(user_id, object_type, object_id)` — verhindert Duplikate, kein Soft-Delete (ein entfernter Favorit wird hart gelöscht, es gibt keine Nachvollziehbarkeitspflicht wie beim Löschregister).

## Events

Publiziert (Stream `favorite`, `ensure_stream=True`):

| event_type | payload |
|---|---|
| `favorite.added` | `{user_id, object_type, object_id}` |
| `favorite.removed` | `{user_id, object_type, object_id}` |

Kein eigener Konsument — dieser Service reagiert auf keine Events anderer Services.

**Audit-Anbindung**: Audit Service konsumiert seit dieser Session zusätzlich `favorite.>` (gleiches Sofort-Ergänzungs-Muster wie bei jedem vorherigen neuen Producer-Stream).

## Selbst-Registrierung (Konzept 3.2a)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`), identisches Muster wie jeder andere Service. Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`. Das Gateway benötigt keine eigene Codeänderung — Routing läuft vollständig dynamisch über `service_type="favorite-service"`.

## Tests

- `uv run pytest services/favorite-service/tests`: Repository (Anlegen, Duplikat-Ablehnung, Entfernen inkl. `NotFoundError`, Listenfilterung nach Nutzer/Objekttyp, Sortierung neueste zuerst), API (`POST`/`GET`/`DELETE` inkl. `409`/`404`, Filterung nach `object_type`, Nutzer-Isolation). **12 Tests, alle grün.**
- **Live-Smoke-Test** (P7-S1d): siehe `PROGRESS.md` — Dokument/Ordner per Kontextmenü favorisiert, `FavoritesPane` löste Namen korrekt auf, "Öffnen" navigierte für beide Objekttypen korrekt, Audit-Trail zeigte `favorite.added`/`favorite.removed`.

## Offene Punkte

- Keine referenzielle Prüfung gegen document-/folder-service (bewusst, siehe oben) — ein Favorit auf ein zwischenzeitlich gelöschtes Objekt bleibt bestehen, bis der Nutzer ihn manuell entfernt.
- Kein Admin-UI/keine Konfiguration nötig — reines Endnutzer-Feature ohne Vier-Augen-Bezug.
- Keine Umlaufmappen (`case-service`) — bei der Planfreigabe bewusst auf Dokumente/Ordner beschränkt, da Umlaufmappen aktuell keine eigene UI in `user-ui` haben (siehe `PROGRESS.md`).
