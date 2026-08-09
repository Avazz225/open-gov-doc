# dms-connector-sdk

Connector-Architektur (Konzept 3.3, P12-S1): wiederverwendbare DMS-seitige Anbindung für
Connector-Services (WebDAV, CMIS) — kennt nur `folder-service`/`document-service`, kein Protokoll
(WebDAV/CMIS bleibt Sache des jeweiligen Connector-Services selbst).

- **`ConnectorCapability`/`ConnectorDescriptor`** (`capability.py`): die in 3.3 geforderte
  "Capability-Beschreibung" (`read`/`write`/`metadata`/`locking`/`versioning`) als Dataclass,
  liefert `as_capability_list()` für die Selbstregistrierung bei `registry-service`
  (`dms-registry-client`s `capabilities`-Feld ist bereits ein freies `list[str]`, keine
  Schemaänderung nötig, siehe P12-S0-Recherche).
- **`DmsTreeClient`** (`dms_tree_client.py`): bewusst **synchron** (`httpx.Client`, nicht
  `AsyncClient`) — der erste Connector (`webdav-connector`) baut auf `wsgidav` auf, dessen
  `DAVProvider`-Schnittstelle selbst synchron ist (WSGI); eine async-Variante hätte dort eine
  async/sync-Brücke (`asgiref.async_to_sync`) über mehrere verschachtelte Thread-/Loop-Grenzen
  gebraucht - ein bekannt fragiles Muster. Der zweite, FastAPI-basierte Connector (`cmis-connector`,
  P12-S4) nutzt diese Lib trotzdem gefahrlos: FastAPI führt normale `def`-Endpunkte (kein
  `async def`) automatisch in seinem eigenen Threadpool aus; Schreib-Endpunkte, die zusätzlich
  `await request.form()` brauchen, lagern den synchronen SDK-Aufruf explizit über
  `asyncio.to_thread()` aus (siehe `docs/services/cmis-connector.md`).

  Bietet: Pfadauflösung (`resolve_path()`, segmentweise ab `root`, O(Tiefe) HTTP-Aufrufe —
  bewusst einfach, kein Cache mit Invalidierungsproblemen), Direktzugriff per ID
  (`get_folder()`/`get_document()`, seit P12-S2 — für Aufrufer, die eine ID statt eines Pfads
  kennen, z. B. `migration-service`), Ordner-/Dokument-CRUD, PUT-Semantik (`write_document()`
  legt an oder checkt eine neue Version ein, je nachdem ob `existing_document_id` gesetzt ist;
  optionales `comment`-Argument seit P12-S4, weitergereicht an `document-service`s
  `POST /documents/{id}/versions`-Kommentarfeld — Grundlage für CMIS' `checkinComment`),
  Verschieben (`move_document()`/`move_folder()`, nutzt `document-service`s seit P12-S1 neues
  `folder_id`-Feld bzw. `folder-service`s bestehendes `parent_id`), Sperren
  (`acquire_lock()`/`release_lock()`/`get_lock()` — dünne Wrapper um `document-service`s
  bestehende Lock-Endpunkte, **keine eigene Sperrlogik**: eine über einen Connector gesperrte
  Version ist serverseitig dieselbe Sperre, die auch die User-UI sähe).
- **`TreeFolder`/`TreeDocument`** führen seit P12-S4 zusätzlich `created_by`/`created_at` (beide
  Felder waren in den zugrundeliegenden `FolderOut`/`DocumentOut`-Antworten längst vorhanden,
  wurden aber bislang nie in die Dataclasses übernommen — Grundlage für CMIS'
  `cmis:createdBy`/`cmis:creationDate`, für `webdav-connector` weiterhin folgenlos, da dort nicht
  gelesen).

Fehlerfälle sind absichtlich klein gehalten (`PathNotFoundError`, `LockConflictError`,
`LockNotHeldError`) — die eigentliche Protokoll-Übersetzung (WebDAV-Statuscodes, CMIS-Fehlerobjekte)
bleibt Sache des jeweiligen Connector-Services, nicht dieser Lib.

Siehe `services/webdav-connector/` für die erste Referenzimplementierung (P12-S1),
`services/cmis-connector/` für die zweite (P12-S4, von Hand implementierte CMIS-1.1-Browser-
Binding statt einer Bibliothek — siehe ADR 0036, warum keine gepflegte Python-CMIS-Server-Lib
existiert) und `services/migration-service/` für einen dritten Nutzer (P12-S2, liest die
Quellinstallation über diese Lib und nutzt dieselbe Lib erneut auf der Zielinstallation, um
Empfangenes tatsächlich anzulegen).
