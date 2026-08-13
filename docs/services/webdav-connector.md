# webdav-connector

**Verantwortung:** Erster Referenz-Connector der Connector-Architektur (Konzept 3.3, P12-S1) — macht `folder-service`/`document-service` über das WebDAV-Protokoll (RFC 4918) als Netzlaufwerk ansprechbar (Windows-Explorer/macOS-Finder/Word). Das DMS ist dabei der WebDAV-**Server** (kein Client eines externen Repositories) — siehe "Richtungsentscheidung" unten. Zweiter Referenz-Connector: [`cmis-connector`](cmis-connector.md) (P12-S4).

**Konzept-Referenz:** 3.3, 4.2
**Kein eigenes Postgres-Schema** (stateless — jede Anfrage übersetzt sich live in HTTP-Aufrufe gegen `folder-service`/`document-service`, siehe `libs/dms-connector-sdk`)
**ADR:** [0033 — Server-Richtung, `wsgidav`+`WsgiToAsgi`, synchrones Connector-SDK](../adr/0033-webdav-connector-server-direction-and-wsgidav.md)

## Richtungsentscheidung (server vs. client)

Konzept 3.3/3.7/3.8/9/12 sprechen an mehreren Stellen generisch von "Anbindung externer Repositories" — für sich genommen uneindeutig, ob ein Connector das DMS *für* externe Programme oder *gegen* ein externes Repository öffnet. Zwei konkrete Textstellen klären das eindeutig zugunsten **Server**:

- **Konzept 4.2**: "Öffnet ein Nutzer ein Dokument aus einer Anwendung heraus (z. B. Word über WebDAV/CMIS-Anbindung) zur Bearbeitung, wird automatisch ein Bearbeitungs-Lock gesetzt."
- **ADR 0002** (Dokument-Sperren): "Entspricht dem etablierten Optimistic-Concurrency-/ETag-Muster aus WebDAV/CMIS, mit dem das Dokument ohnehin über externe Anwendungen angesprochen wird."

Beide beschreiben ein externes Programm, das das DMS über WebDAV *anspricht* — nicht das DMS, das einen fremden WebDAV-Server abfragt. `webdav-connector` implementiert entsprechend einen WebDAV-Server.

## Architektur

| Baustein | Wahl | Begründung |
|---|---|---|
| Protokoll-Engine | [`wsgidav`](https://github.com/mar10/wsgidav) (MIT, aktiv gepflegt) statt eigener Implementierung | WebDAV inkl. Windows-Explorer-/Finder-Kompatibilität selbst zu bauen (Depth-Header, If-Header, Lock-Token-Semantik, viele klientenspezifische Macken) ist ein eigenes, fehleranfälliges Projekt. `wsgidav` hat einen dokumentierten Erweiterungspunkt für Nicht-Dateisystem-Backends (`DAVProvider`/`DAVCollection`/`DAVNonCollection`). |
| WSGI in einem sonst durchgehend ASGI/FastAPI-Projekt | `asgiref.wsgi.WsgiToAsgi` mountet die wsgidav-App unter `/webdav` **innerhalb** eines normalen FastAPI-Service (`app.mount()`) | Der Service bleibt äußerlich konsistent mit allen anderen Services (`/healthz`, `BaseServiceSettings`, Registry-Selbstregistrierung, Lizenzprüfung laufen als normale FastAPI-Routen) — nur `/webdav/*` läuft durch die gebrückte wsgidav-Engine. |
| DMS-Baum-Übersetzung | Eigene Lib `libs/dms-connector-sdk` (`DmsTreeClient`), nicht Code im Connector selbst | Konzept 3.3 verlangt ein wiederverwendbares SDK — der künftige CMIS-Connector (P12-S4) braucht dieselbe DMS-seitige Logik, nur eine andere Protokoll-Schicht obendrauf. |
| WebDAV LOCK/UNLOCK | Direkt auf `document-service`s bestehende Sperr-Endpunkte (`POST`/`DELETE`/`GET /documents/{id}/lock`, 4.2) | Kein zweites, konkurrierendes Sperrsystem — eine über WebDAV gesperrte Datei ist serverseitig dieselbe Sperre, die auch die User-UI sieht. |

**`DmsTreeClient` ist bewusst synchron** (`httpx.Client`, nicht `AsyncClient`): wsgidavs `DAVProvider`-Schnittstelle ist selbst synchron (WSGI). Siehe `libs/dms-connector-sdk/README.md` für die Begründung.

## `mount_path` (wichtige wsgidav-Falle)

`WsgiToAsgi` mountet die wsgidav-App unter `settings.webdav_mount_path` (Default `/webdav`) — wsgidav selbst weiß davon nichts und würde ohne den Konfigurationsschlüssel `mount_path` Hrefs relativ zu seiner **eigenen** Wurzel (`/...`) statt zur tatsächlich öffentlichen URL (`/webdav/...`) ausliefern. WebDAV-Clients, die den Ressourcennamen durch Abschneiden des Mount-Präfixes vom Href bestimmen (z. B. `webdav4`), erhalten dann verstümmelte Namen (z. B. `Ordner-abc123` → `bc123`, je nach Präfixlänge) statt eines Fehlers — ein bei der Implementierung real aufgetretener, schwer zu diagnostizierender Bug, da `wsgidav` selbst valide antwortet und die Verstümmelung erst beim Client entsteht. Fix: `"mount_path": settings.webdav_mount_path` im `WsgiDAVApp`-Konfigurationsdict (`main.py`).

## Datei-Metadaten kommen aus der Versionstabelle, nicht aus `DocumentOut`

`document-service`s `DocumentOut` (Antwort von `GET/POST/PATCH /documents...`) trägt keine Datei-Metadaten (Größe/Content-Type/Prüfsumme) — die leben ausschließlich auf `DocumentVersionOut` der jeweils aktuellen Version (`GET /documents/{id}/versions/{version_number}`). `DmsTreeClient` holt diese deshalb bei jeder `TreeDocument`-Konstruktion (Anlegen, Einchecken, Verschieben, Auflisten) mit einem zusätzlichen Aufruf nach (`_fetch_current_version`) — ein zusätzlicher HTTP-Roundtrip je Dokument in einer Verzeichnisauflistung, für eine Referenzimplementierung bewusst in Kauf genommen: WebDAV-Clients (Explorer/Finder) verlassen sich auf korrekte `Content-Length`/`ETag`-Werte, ein falscher Defaultwert (`0`/leerer String) wäre die schlechtere Alternative — ein leerer String als ETag lässt wsgidavs eigene Validierung (`checked_etag`) sogar mit `500` fehlschlagen (nur `None` oder ein nicht-leerer String sind gültig), ebenfalls real aufgetreten und Grund, warum `checksum_sha256` als `str | None` statt mit leerem-String-Default modelliert ist.

## Schreiben: Puffer wird beim Schließen abgegriffen, nicht danach gelesen

wsgidavs echter `do_PUT`-Handler (`request_server.py`) ruft `fileobj.close()` auf den von `begin_write()` zurückgegebenen Puffer, **bevor** er `end_write()` aufruft. Ein `BytesIO.getvalue()` erst in `end_write()` würde auf einem bereits geschlossenen Puffer `ValueError: I/O operation on closed file` werfen — real aufgetreten, weil eine direkte Python-Reproduktion (ohne den echten HTTP/WSGI-Pfad) das `close()` nie aufrief und den Bug deshalb nicht zeigte. `DmsDavDocument` nutzt daher `_CapturingBuffer`, eine `BytesIO`-Unterklasse, die den Inhalt beim `close()` abgreift, statt ihn erst danach aus dem (dann geschlossenen) Puffer zu lesen.

## Authentifizierung

`DmsAuthDomainController` (`wsgidav.dc.base_dc.BaseDomainController`) bildet WebDAV-Basic-Auth (das, was Explorer/Finder/Word beim Verbinden mit einem Netzlaufwerk senden) auf `auth-service`s bestehendes `POST /login` ab — kein zweiter, connector-eigener Nutzerspeicher. Bewusst nicht anonym erreichbar: anders als die übrigen Backend-Services (deren Ports laut ADR 0005 nur aus Entwickler-Komfort direkt offen sind, echte Nutzung läuft über das authentifizierende Gateway) ist ein WebDAV-Connector sein eigener, direkt von externen Programmen angesprochener Endpunkt — ohne echte Authentifizierung hier wäre jedes Dokument für jeden mit Netzwerkzugriff lesbar/schreibbar. Digest-Auth ist deaktiviert (Basic über TLS-Termination in der Zielumgebung gilt als ausreichend).

## Office-Direktbearbeitung: `by-id`-Pfad + Edit-Token (Ad-hoc Post-Roadmap, siehe ADR 0061)

Zwei additive Ergänzungen, ohne den bestehenden pfadbasierten Fluss zu ändern:

- **`DmsDavProvider.get_resource_inst()`** erkennt Pfade mit Präfix `by-id/` (z. B. `by-id/<document-id>.docx`) VOR dem üblichen `resolve_path()`-Baumdurchlauf und löst sie direkt über `self.tree.get_document(document_id)` auf — O(1) statt O(Baumtiefe). Die `.ext`-Endung ist rein kosmetisch (Office' Dateityperkennung beim Öffnen) und wird serverseitig verworfen.
- **`DmsAuthDomainController.basic_auth_user()`** behandelt ein leeres Passwort als Zeichen dafür, dass der übergebene Benutzername ein `document-service`-`WebdavEditToken` ist, nicht ein echter Benutzername: löst es gegen `GET /internal/webdav-edit-tokens/{token}` auf (Ost-West, direkt gegen `document_service_base_url`, kein Umweg über `/login`) und überschreibt `environ["wsgidav.auth.user_name"]` mit der aufgelösten `principal_id` — nicht das rohe Token stehen lassen, sonst würde ein späterer Check-in fälschlich das Token statt der echten Identität als Sperrinhaber verwenden. Der bestehende Benutzername+Passwort-Zweig (echter Netzlaufwerk-Mount) bleibt unverändert.

Zusammen ergeben beide die Zieladresse für den Office-URI-Handler (`user-ui`): `https://<token>:@<host>/webdav/by-id/<document-id>.<ext>`.

## Lizenzierung (3.3, P9-S2-Muster)

Konzept 3.3 nennt Connectoren wörtlich als Beispiel für lizenzierbare Komponenten. `registry-service`s `licensable_components` enthält `"webdav-connector": "demo"` (identisches Muster wie `workflow-service`, siehe `docs/services/registry-service.md`). Da der eigentliche WebDAV-Verkehr nicht über FastAPI-Routen läuft (kein `Depends()`-Gate möglich), prüft `DmsDavProvider.check_license(action)` direkt in den wsgidav-Callback-Methoden: `"unlicensed"` blockiert jeden Zugriff (`get_resource_inst`), `"demo"` blockiert nur Schreiboperationen (`create_collection`, `end_write`, `handle_delete`, `handle_move` — jeweils auf Ordner und Dokument).

## Ordner-Lock-Mapping

Eine per WebDAV gesperrte Sitzung hat kein natives Session-Konzept wie ein Browser-Login (Basic-Auth ist pro Request neu). Die `session_id` für `document-service`s Lock-Endpunkte ist deshalb pro Nutzername stabil (`webdav:<username>`), nicht pro TCP-Verbindung — ausreichend, da `document-service` Sperren ohnehin pro Dokument führt. **Bewusste Grenze**: eine über ein echtes WebDAV-LOCK gehaltene Sperre wird nicht über die gesamte Bearbeitungsdauer gespiegelt, nur während jeder einzelnen Schreiboperation (`end_write()` erwirbt die Sperre, hält sie für die Dauer des Uploads, gibt sie im `finally` wieder frei) — ein per WebDAV geöffnetes Word-Dokument hält document-services Sperre also nicht durchgehend zwischen Öffnen und Speichern, sondern nur während des eigentlichen Speichervorgangs.

## Konfiguration

| Variable | Default | Bedeutung |
|---|---|---|
| `DMS_DOCUMENT_SERVICE_BASE_URL` | `http://localhost:8006` | `document-service`-Adresse |
| `DMS_FOLDER_SERVICE_BASE_URL` | `http://localhost:8008` | `folder-service`-Adresse |
| `DMS_AUTH_SERVICE_BASE_URL` | `http://localhost:8003` | Für `DmsAuthDomainController`s `POST /login`-Prüfung |
| `DMS_WEBDAV_ROOT_FOLDER_ID` | `root` | DMS-Ordner, der als WebDAV-Wurzel erscheint |
| `DMS_WEBDAV_MOUNT_PATH` | `/webdav` | Mount-Präfix, siehe "wsgidav-Falle" oben |
| `WEBDAV_CONNECTOR_PORT` | `8027` | Host-Port im Dev-Compose-Stack |

Mounten im Dev-Stack z. B. via `net use`/"Netzlaufwerk verbinden" auf `http://localhost:8027/webdav/`.

## Bewusste Grenze: keine GUI-Client-Verifikation

Getestet wurde diese Session über einen echten WebDAV-**Client** (`webdav4`, MIT, nur Test-Abhängigkeit) gegen die laufende Instanz — PROPFIND/GET/PUT/MKCOL/MOVE/LOCK/DELETE, kein Mocking des Protokolls. Echte Windows-Explorer-/macOS-Finder-Kompatibilität konnte in dieser Umgebung (kein GUI-Client verfügbar) nicht getestet werden — `wsgidav` ist die Engine, die in der Praxis dafür verwendet/getestet wird, ein Mensch sollte das vor Produktivnutzung einmal real mounten.

## Offene Punkte

- Kein eigener `GET /metrics` (10.1) — als reiner Protokoll-Übersetzer ohne eigene Geschäftsdaten aktuell kein eigener Sensor definiert.
- Property-/Attribut-Zugriff (`object_type`-Attribute) ist über WebDAV nicht abbildbar (RFC 4918 kennt nur Dead-Properties, keine strukturierten Custom-Metadaten wie das DMS sie kennt) — Attribute bleiben ausschließlich über die User-UI/API sichtbar, nicht über den WebDAV-Connector.
