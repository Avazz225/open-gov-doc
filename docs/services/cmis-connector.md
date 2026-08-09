# cmis-connector

**Verantwortung:** Zweiter Referenz-Connector der Connector-Architektur (Konzept 3.3, P12-S4) —
macht `folder-service`/`document-service` über eine selbst implementierte **CMIS 1.1 Browser
Binding** (Kapitel 5 der OASIS-CMIS-1.1-Spezifikation) ansprechbar. Das DMS ist dabei der
CMIS-**Server** (gleiche Richtungsentscheidung wie `webdav-connector`, ADR 0033/0002, Konzept 4.2).
Erster Referenz-Connector: [`webdav-connector`](webdav-connector.md) (P12-S1).

**Konzept-Referenz:** 3.3, 4.2
**Kein eigenes Postgres-Schema** (stateless — jede Anfrage übersetzt sich live in HTTP-Aufrufe
gegen `folder-service`/`document-service`, über dieselbe `libs/dms-connector-sdk` wie
`webdav-connector`)
**ADR:** [0036 — Von Hand implementierte Browser Binding statt Bibliothek](../adr/0036-cmis-connector-from-scratch-browser-binding.md)

## Warum von Hand (keine Bibliothek)

Anders als bei WebDAV (`wsgidav`, aktiv gepflegt) existiert **keine gepflegte Python-CMIS-*Server*-
Bibliothek** — reale Recherche (P12-S4-Kickoff) ergab: alle nennenswerten Python-CMIS-Pakete
(`cmislib`, `cmislib3`, `cmislib-maykin`, `CMIS.PythonLib`, `drc-cmis`) sind reine **Client**-
Bibliotheken, die meisten davon seit Jahren unverändert. Das einzige reale Server-Framework
(Apache Chemistys OpenCMIS) ist Java-only, es gibt kein Python-Äquivalent. Details/Begründung
siehe ADR 0036.

## Umfang (Referenzimplementierung, kein vollständiger CMIS-1.1-Server)

| Kategorie | Implementiert | Bewusst nicht implementiert |
|---|---|---|
| Repository | `repositoryInfo` (Service-URL + Repository-URL) | Multi-Repository (immer genau ein Repository `default`), `typeChildren`/`typeDescendants`/`typeDefinition` (kein CMIS-Typsystem für eigene Objekttypen) |
| Navigation | `children`, `object` (per Id oder Pfad) | `descendants`, `folderTree`, `parent(s)`, `checkedout`-Liste |
| Objekt lesen | `content` (Content-Stream) | `renditions`, `allowableActions`, `relationships`, `policies`, `acl` |
| Objekt schreiben | `createDocument`, `createFolder`, `update` (nur `cmis:name`), `move`, `delete`, `deleteTree`, `setContent` | `createDocumentFromSource`, `createRelationship`/`createPolicy`/`createItem`, `appendContent`, `deleteContent`, `addObjectToFolder`/`removeObjectFromFolder`, `applyPolicy`/`applyACL`, `bulkUpdate` |
| Versionierung | `checkOut`, `cancelCheckOut`, `checkIn` (immer mit neuem Inhalt) | Volle Versionshistorie/`getAllVersions`, inhaltsloses Checkin |
| Suche | — | CMIS-Query-Sprache (`query`-Selector/-Action) — Konzept 3.7/ADR 0012 setzt für die eigentliche Volltextsuche ohnehin auf Postgres FTS, nicht CMIS SQL |

Insgesamt ~14 Endpunkte (repositoryInfo/children/object/content lesend, die zehn genannten
Aktionen schreibend) — vergleichbar im Umfang mit dem WebDAV-Kernmethodenset aus P12-S1, aber ohne
eine `wsgidav`-Entsprechung zum Anlehnen (siehe ADR 0036).

## Object-URL-Auflösung (5.3.4)

Ein Objekt wird per `objectId`-Query-Parameter ODER per Pfad adressiert, der an die Root-Folder-
URL angehängt ist (`objectId` hat Vorrang, wörtlich aus der Spezifikation). Bei **Schreibaktionen**
gibt es zusätzlich das Formular-Control `objectId` (5.4.4.3.3) — das adressiert bei `update`/
`move`/`delete`/`setContent`/`checkOut`/`cancelCheckOut`/`checkIn` das zu bearbeitende Objekt,
während bei `createDocument`/`createFolder` (die kein solches Control kennen) weiterhin die
URL selbst (Query-`objectId` oder Pfad) den Zielordner bestimmt. `cmis_connector.resolve` bildet
beide Fälle einheitlich ab; ein **echter, bei der Live-Verifikation gefundener Bug**: die
POST-Routen lasen `objectId` anfangs nur aus dem Formular, nie aus dem Query-String — `createDocument`
landete dadurch immer im Wurzelordner, unabhängig vom adressierten Zielordner. Fix: beide Quellen
werden gelesen, das Formular-Control hat Vorrang, falls beide vorkommen.

## Kein separates Private-Working-Copy-Objekt

`document-service` kennt keine "Working Copy" als eigenständige Entität — die reale
`document-service`-Sperre (4.2) übernimmt exakt die Rolle, die CMIS dem PWC-Mechanismus zuschreibt
(kein Schreibzugriff durch andere bis Checkin/CancelCheckout). `checkOut` ruft daher
`acquire_lock()` auf dem Original-Dokument auf, `checkIn`/`cancelCheckOut` entsprechend
`write_document()`+`release_lock()`/`release_lock()` — die zurückgegebene "PWC"-Objekt-Id ist
bewusst **identisch** zur Original-Dokument-Id (echte CMIS-Server geben hier eine eigene Id
zurück, dieses DMS hat aber kein zweites Objekt, das diese Id tragen könnte). Ein Checkout-Konflikt
(zweiter Akteur versucht, ein bereits ausgechecktes Dokument erneut auszuchecken) wird über
`document-service`s bestehende `LockConflictError` erkannt und als CMIS-`updateConflict` (409)
gemeldet — **wichtig**: die Sperrprüfung vergleicht `locked_by` (den Akteur), nicht `session_id`,
derselbe Akteur kann seine eigene Sperre daher jederzeit erneut "erwerben" (idempotent), nur ein
ANDERER Akteur löst einen echten Konflikt aus.

## `delete` auf einem nicht-leeren Ordner (echter Fund)

`folder-service`s Hard-Delete-Endpunkt (`DELETE /folders/{id}`) prüft nur eigene Unterordner auf
Leere, **nicht** Dokumente — Dokumente leben in einem komplett anderen Service/Schema
(`document-service`) und werden dort nie gegengeprüft. Das war bislang nie sichtbar, weil der
einzige bisherige Aufrufer (`webdav-connector`s `DmsDavFolder.handle_delete()`) immer erst alle
Kinder rekursiv löscht, bevor er den (dann garantiert leeren) Ordner selbst löscht — CMIS' `delete`-
Aktion ist dagegen laut Spezifikation ein **nicht-kaskadierender** Einzelobjekt-Löschversuch, der
bei einem nicht-leeren Ordner mit `constraint` (409) fehlschlagen MUSS. `cmis-connector` prüft
deshalb selbst (`_tree.list_children()`) auf Unterordner UND Dokumente, bevor es
`delete_folder()` überhaupt aufruft — kein Eingriff in `folder-service` nötig, die Prüfung gehört
hierher (Konsequenz des CMIS-Vertrags, nicht ein allgemeiner Mangel von `folder-service`s
Hard-Delete-Fallback).

## Nur `cmis:name` als schreibbares Property

Diese Referenzimplementierung bildet ausschließlich `cmis:name` (Umbenennen) auf ein
DMS-Attribut ab. Eigene Objekttyp-Attribute (2.2, `object-type-service`) werden nicht als
CMIS-Properties exponiert — das würde ein vollständiges CMIS-Typsystem (`typeDefinition` je
Objekttyp, Property-Definitionen mit CMIS-Datentypen) erfordern, siehe "Umfang" oben.

## Authentifizierung

HTTP-Basic-Auth (5.2.9.1 "Basic Authentication for Non-Browser Clients") gegen `auth-service`s
bestehendes `POST /login` — identisches Muster wie `webdav-connector`s `DmsAuthDomainController`,
hier als FastAPI-Dependency (`cmis_connector.auth.parse_basic_auth`/`require_actor`) statt
wsgidav-`BaseDomainController`. Fehlender `Authorization`-Header → `401` mit
`WWW-Authenticate: Basic` (Challenge, ermöglicht echten CMIS-Clients einen normalen Basic-Auth-
Dialog); falsche Zugangsdaten → `403` (verhindert Browser-Login-Popups bei versehentlichem
Browser-Zugriff, wörtlich von 5.2.9.1 als zulässige Alternative genannt).

## `asyncio.to_thread()` für den synchronen `DmsTreeClient`

Lese-Endpunkte sind normale (nicht-`async`) FastAPI-Routen — `DmsTreeClient` ist synchron (siehe
`libs/dms-connector-sdk/README.md`), FastAPI führt solche Routen automatisch im eigenen
Threadpool aus (wie schon bei `webdav-connector`, ADR 0033). Schreib-Endpunkte MÜSSEN dagegen
`async def` sein (`await request.form()` ist eine Starlette-`async`-only-API, die den
Multipart-Körper streamt) — der eigentliche `DmsTreeClient`-Aufruf läuft deshalb über
`asyncio.to_thread()`, statt ihn direkt im Event-Loop-Thread zu blockieren (der bei ADR 0034 als
künftiger Präzedenzfall für genau diesen Fall festgehaltene Ansatz).

## Erweiterungen an `libs/dms-connector-sdk` (gemeinsam mit `webdav-connector` genutzt)

- `TreeFolder`/`TreeDocument` bekamen `created_by`/`created_at` (beide Felder waren in den
  zugrundeliegenden `FolderOut`/`DocumentOut`-Antworten bereits vorhanden, wurden aber bislang nie
  in die Dataclasses übernommen) — Grundlage für CMIS' `cmis:createdBy`/`cmis:creationDate`. Bei
  `TreeFolder` bewusst `str | None`/`datetime | None`: `resolve_path("")` (Wurzel-Adressierung,
  durchlaufen bei **jeder** WebDAV-Anfrage an die Wurzel) konstruiert weiterhin rein lokal ohne
  HTTP-Aufruf — ein zusätzlicher Roundtrip dort wäre ein spürbarer Performance-Rückschritt, `None`
  ist CMIS' eigener "value not set"-Zustand (5.2.7), kein erfundener Wert.
- `write_document()` bekam ein optionales `comment`-Argument, weitergereicht an
  `POST /documents/{id}/versions`s bereits bestehendes `comment`-Formularfeld — Grundlage für
  CMIS' `checkinComment`.
- **Realer Performance-Befund bei der Live-Verifikation, kein Code-Bug**: `webdav-connector`s
  Testsuite schlug im vollen `--build`-Regressionslauf mit `httpx.ReadTimeout` bei PROPFIND auf
  die Wurzel fehl — verursacht durch über die gesamte, sehr lange Projektlaufzeit angesammelte
  Testdaten direkt unter `root` (68 Ordner + 74 Dokumente, aus mehreren Connectoren/Services'
  eigenen, sich nie selbst aufräumenden Testläufen). `DmsTreeClient.list_children()` holt bewusst
  je Dokument einen zusätzlichen HTTP-Aufruf nach (siehe deren Docstring) — bei 74 Dokumenten
  direkt an der Wurzel dauerte eine einzelne Anfrage über 10 Sekunden. Behoben durch einmaliges
  Aufräumen (`POST /folders/{id}/trash`/`DELETE /documents/{id}` für alle 142 Root-Objekte,
  ausnahmslos an Test-Actor-Namen erkennbar), keine Code-Änderung nötig — reduzierte dieselbe
  Anfrage auf 76ms. Details siehe ADR 0036.

## Lizenzierung (3.3/9.1, P9-S2-Muster)

Konzept 9.1 nennt "CMIS-Connector" wörtlich als Beispiel für eine separat lizenzierbare
Komponente. `registry-service`s `licensable_components` enthält `"cmis-connector": "demo"`
(identisches Muster wie `webdav-connector`/`migration-service`) — Demo-Modus blockiert nur
Schreibaktionen, `unlicensed` blockiert jeden Zugriff.

## Konfiguration

| Variable | Default | Bedeutung |
|---|---|---|
| `DMS_DOCUMENT_SERVICE_BASE_URL` | `http://localhost:8006` | `document-service`-Adresse |
| `DMS_FOLDER_SERVICE_BASE_URL` | `http://localhost:8008` | `folder-service`-Adresse |
| `DMS_AUTH_SERVICE_BASE_URL` | `http://localhost:8003` | Für die Basic-Auth-Prüfung |
| `DMS_CMIS_ROOT_FOLDER_ID` | `root` | DMS-Ordner, der als CMIS-Root-Folder erscheint |
| `DMS_CMIS_REPOSITORY_ID` | `default` | Repository-Id (immer genau ein Repository) |
| `CMIS_CONNECTOR_PORT` | `8030` | Host-Port im Dev-Compose-Stack |

Beispiel-Aufruf (Browser-URL-Muster): `http://localhost:8030/browser/default/root?cmisselector=children`.

## Tests

Läuft wie `webdav-connector`/`migration-service` gegen den echten, laufenden Container (kein
In-Prozess-`TestClient`, kein Mocking der Nachbar-Services) — `real_user`/`second_real_user`-
Fixtures legen echte `auth-service`-Konten an (letzteres für den Checkout-Konflikt-Test, der zwei
unterschiedliche Akteure braucht). Deckt ab: Basic-Auth-Challenge/Ablehnung, Repository-Info,
Children-Listing, Objekt-per-Id, Content-Stream (inkl. Default-Selektor), Umbenennen, Verschieben,
`setContent`-Versionierung, vollständiger Checkout→Checkin-Zyklus, Checkout-Konflikt,
CancelCheckout, Löschen (Dokument, nicht-leerer Ordner → `constraint`), kaskadierendes
`deleteTree`.

## Bewusste Grenzen

- **Keine GUI-Client-Verifikation** — getestet über direkte HTTP-Aufrufe (rohes Browser-Binding-
  Wire-Format, kein Mocking), nicht über einen echten CMIS-Desktop-/Office-Client (kein solcher
  in dieser Umgebung verfügbar) — gleiche, bereits bei `webdav-connector` dokumentierte Grenze.
- **Nur succinct-Properties** (5.2.11) — keine typannotierten `properties`-Objekte mit
  Property-Definitionen, siehe "Nur `cmis:name`..." oben.
- **Kein CMIS-Query** — Volltextsuche läuft laut ADR 0012 ohnehin über Postgres FTS
  (`search-service`), nicht über CMIS SQL.
- **Inhaltsloses Checkin nicht möglich** — `document-service`s Versions-Endpunkt verlangt je
  Version zwingend eine Datei.
