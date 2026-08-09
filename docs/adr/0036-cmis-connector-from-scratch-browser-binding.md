# 0036 — CMIS Connector: von Hand implementierte Browser Binding statt Bibliothek

**Status:** akzeptiert
**Kontext:** P12-S4 (Konzept 3.3, "Connector-Architektur"). Zweiter Referenz-Connector nach
`webdav-connector` (P12-S1, ADR 0033) — bei P12-S0/P12-S1 bewusst zurückgestellt und als eigene
Session in die Roadmap aufgenommen. Anders als bei WebDAV (`wsgidav`, aktiv gepflegte
Protokoll-Engine) stellte sich die Scope-Frage erst nach echter Recherche: **gibt es überhaupt
eine Python-Bibliothek, auf der ein CMIS-Server aufbauen kann?**

## Recherche-Befund (blockierend für jede Implementierungsentscheidung)

Ein dediziert dafür beauftragter Rechercheauftrag ergab: **keine gepflegte Python-CMIS-*Server*-
Bibliothek existiert irgendwo.**

- **Alle gefundenen Python-CMIS-Pakete sind Client-Bibliotheken**, kein einziges implementiert
  die Server-Seite: `cmislib` (letztes Release vor Jahren, unverändert), `cmislib3`/`cmislib-maykin`
  (Forks desselben unveränderten Codes), `CMIS.PythonLib` (unveränderte Alt-Bibliothek). Einzige
  Ausnahme mit echter, aktueller Pflege: `drc-cmis` (Maykin Media, EUPL-1.2) — aber auch das ist
  ein reiner Client-Adapter, keine Server-Implementierung.
- **Apache Chemistys OpenCMIS**, das einzige reale, vollständige CMIS-Server-Framework, ist
  **Java-only** — nie ein Python-Äquivalent produziert.
- Die einzigen real existierenden CMIS-**Server**-Implementierungen überhaupt (NemakiWare,
  Java/AGPL-3.0; ein .NET-Core-`CmisServer`) sind weder Python noch mit diesem Projekt
  lizenzkompatibel (AGPL-3.0 wäre für eine eingebettete Bibliothek problematisch, .NET ist keine
  Option in einem Python-Projekt).

**Konsequenz**: anders als bei WebDAV gibt es kein Äquivalent zu "eine Bibliothek nehmen und nur
das Backend anschließen" — die gesamte Protokoll-Schicht (URL-Muster, `cmisselector`/`cmisaction`-
Dispatch, succinct-Property-Serialisierung, Formular-Encoding) musste aus der OASIS-CMIS-1.1-
Spezifikation selbst (Kapitel 5, "Browser Binding") direkt nachgebaut werden.

## Entscheidung

**Browser Binding statt AtomPub/SOAP** (bereits bei P12-S0 als Empfehlung festgehalten, jetzt
umgesetzt): von den drei CMIS-1.1-Bindings ist Browser Binding das mit Abstand einfachste (JSON +
HTML-Formular-Semantik über reines GET/POST, keine XML-Namespace-/AtomPub-Feed-Komplexität) — mit
`FastAPI` bereits nativ passend (JSON-Responses, `Form`/`multipart`-Parsing), ohne dass eine
XML-Bibliothek für AtomPub-Entries gebraucht würde.

**Nur succinct-Properties** (5.2.11) statt der vollen, typannotierten `properties`-Repräsentation
— reduziert die zu implementierende JSON-Schemafläche erheblich, ist selbst laut Spezifikation
der von realen Clients bevorzugte, kompaktere Modus.

**Bewusst begrenzter Funktionsumfang** (~14 Endpunkte statt aller ~38 in Kapitel 5.4 aufgeführten
Selektoren/Aktionen): Repository-Info, Children/Object/Content lesend; createDocument/
createFolder/update/move/delete/deleteTree/setContent/checkOut/cancelCheckOut/checkIn schreibend.
Ausgelassen: Typsystem-Introspektion (`typeChildren`/`typeDescendants`/`typeDefinition` — dieses
DMS hat kein CMIS-kompatibles Objekttyp-System, siehe unten), Relationships/Policies/Items/ACL
(keine dieser CMIS-Objektarten hat eine DMS-Entsprechung), CMIS-Query-Sprache (Volltextsuche läuft
laut ADR 0012 ohnehin über Postgres FTS), volle Versionshistorie (`document-service`s
Checkin-Historie wird bereits vom Konzept als vollständig genug behandelt, siehe
`webdav-connector`s Versionierungs-Mapping). Vergleichbar im Umfang mit dem WebDAV-Kernmethodenset
aus P12-S1.

**Kein separates Private-Working-Copy-Objekt bei checkOut/checkIn/cancelCheckOut**:
`document-service` kennt keine "Working Copy" als eigene Entität — die reale Dokumentsperre (4.2)
übernimmt exakt die vom CMIS-PWC-Mechanismus verlangte Rolle (kein Fremdzugriff bis
Checkin/CancelCheckout). Die zurückgegebene "PWC"-Objekt-Id ist deshalb bewusst identisch zur
Original-Dokument-Id, statt eine zweite, künstliche Id zu erfinden, die auf kein reales zweites
Objekt verweisen würde.

**`objectId` wird sowohl aus dem URL-Query-String als auch aus dem POST-Formular gelesen**
(5.3.4 vs. 5.4.4.3.3) — beide Adressierungswege sind laut Spezifikation gültig, je nach Aktion
(Formular-Control für die meisten Schreibaktionen, URL-Adressierung für `createDocument`/
`createFolder`, die kein `objectId`-Formular-Control kennen).

**`asyncio.to_thread()` für den synchronen `DmsTreeClient`-Aufruf in Schreib-Endpunkten**: Lese-
Endpunkte sind normale (nicht-`async`) FastAPI-Routen (automatisch im Threadpool, wie schon
`webdav-connector`), Schreib-Endpunkte müssen aber `async def` sein (`await request.form()` ist
Starlette-`async`-only) — der eigentliche synchrone SDK-Aufruf läuft deshalb über
`asyncio.to_thread()` statt direkt im Event-Loop-Thread, exakt der bei ADR 0034 als künftiger
Präzedenzfall für "synchrone `dms-connector-sdk`-Aufrufe aus `async def`-Endpunkten" festgehaltene
Ansatz.

**Zwei kleine, rückwärtskompatible Erweiterungen an `libs/dms-connector-sdk`** (gemeinsam mit
`webdav-connector` genutzt): `TreeFolder`/`TreeDocument` bekamen `created_by`/`created_at` (in den
zugrundeliegenden `FolderOut`/`DocumentOut`-Antworten längst vorhanden, aber bislang nie in die
Dataclasses übernommen — Grundlage für `cmis:createdBy`/`cmis:creationDate`, bei `TreeFolder`
bewusst `str | None`/`datetime | None` statt verpflichtender Felder, siehe "Konsequenzen"); `write_
document()` bekam ein optionales `comment`-Argument (Grundlage für `cmis:checkinComment`).

## Begründung

- **Browser Binding statt der beiden anderen Bindings**: AtomPub/SOAP hätten eine XML-Bibliothek
  plus deutlich mehr Boilerplate (Namespace-Handling, Feed-/Entry-Strukturen) gebraucht, ohne
  einen Mehrwert für diese Referenzimplementierung zu bieten — Browser Binding ist die von der
  Spezifikation selbst als "einfachste, für moderne Web-Stacks optimierte" Variante beschriebene.
- **Kein CMIS-Typsystem für eigene Objekttypen**: `object-type-service`s Objekttypen (2.2) haben
  ein eigenes, bereits vollständiges Attribut-/Constraint-Modell — ein zusätzliches, paralleles
  CMIS-Typsystem (mit eigenen Property-Definitionen je Attributtyp) zu spiegeln wäre ein
  eigenständiges, großes Feature ohne klaren Konzept-Auftrag (3.3 verlangt "Anbindung", nicht
  "vollständige bidirektionale Typsystem-Synchronisation").
- **PWC-Id = Original-Id statt erfundener zweiter Id**: eine erfundene zweite Id müsste auf ein
  reales zweites Objekt verweisen können (z. B. für einen nachfolgenden `getObject`-Aufruf auf die
  PWC) — ohne echtes zweites Objekt wäre das nur ein Etikettenschwindel, der bei jedem
  Folgeaufruf sofort als `objectNotFound` aufgeflogen wäre.

## Konsequenzen

- **Echter, bei der Live-Verifikation gefundener Bug (vor Testabschluss behoben)**: die
  Schreib-Routen lasen `objectId` anfangs ausschließlich aus dem Formular, nie aus dem
  Query-String — `createDocument`/`createFolder` landeten dadurch immer im Wurzelordner,
  unabhängig vom über die URL adressierten Zielordner (real durch zwei fehlschlagende Tests
  aufgedeckt: ein vermeintlich nicht-leerer Ordner ließ sich trotzdem löschen, ein per `deleteTree`
  kaskadiert gelöschtes Dokument blieb unverändert). Fix: beide Adressierungswege werden gelesen,
  das Formular-Control hat Vorrang.
- **Echter, bei der Live-Verifikation gefundener Bug in `folder-service`s Domäne (im Connector
  kompensiert, nicht in `folder-service` selbst behoben)**: `DELETE /folders/{id}` prüft nur
  eigene Unterordner auf Leere, nie Dokumente (die in einem anderen Service/Schema leben) — bislang
  nie sichtbar, weil der einzige bisherige Aufrufer (`webdav-connector`) immer erst rekursiv alle
  Kinder löscht. CMIS' nicht-kaskadierende `delete`-Aktion MUSS aber bei einem Dokument-haltigen
  Ordner mit `constraint` (409) fehlschlagen — `cmis-connector` prüft das selbst
  (`_tree.list_children()`), bevor es `delete_folder()` überhaupt aufruft.
- **`TreeFolder.created_by`/`created_at` bewusst optional (`| None`), nicht verpflichtend**:
  `resolve_path("")` (leerer Pfad, Wurzel-Adressierung) konstruiert die Wurzel weiterhin rein
  lokal, ohne HTTP-Aufruf — dieser Zweig läuft bei **jeder** WebDAV-Anfrage an die Wurzel durch
  (`get_resource_inst("/")`), ein zusätzlicher Roundtrip wäre dort ein spürbarer Performance-
  Rückschritt. Ein erster Versuch, stattdessen `get_folder()` echt aufzurufen, wurde bei der
  Live-Verifikation wieder verworfen (siehe nächster Punkt) — `created_by`/`created_at` bleiben in
  diesem einen Fall `None` (CMIS' eigener "value not set"-Zustand, 5.2.7) statt erfundener Werte.
- **Realer Performance-Befund bei der Live-Verifikation** (kein Code-Bug): `webdav-connector`s
  Testsuite schlug nach dem `--build`-Vollregressionslauf mit `httpx.ReadTimeout` bei PROPFIND auf
  die WebDAV-Wurzel fehl. Ursache war **nicht** die testweise eingeführte `get_folder()`-Variante
  (nach deren Rücknahme trat der Timeout unverändert weiter auf) und **kein** Deadlock, sondern
  über die gesamte, sehr lange Projektlaufzeit dieser Konversation im geteilten Dev-Root-Ordner
  angesammelte Testdaten: 68 Unterordner + 74 Dokumente direkt unter `root` (u. a. aus
  `webdav-connector`s, `cmis-connector`s und `migration-service`s jeweils eigenen Testläufen,
  keiner davon räumt seine bei `root` erzeugten Objekte selbst wieder auf). `DmsTreeClient.
  list_children()` holt bewusst je Dokument einen zusätzlichen HTTP-Aufruf nach (siehe deren
  Docstring) — bei 74 Dokumenten allein an der Wurzel dauerte eine einzelne Root-PROPFIND-Anfrage
  dadurch über 10 Sekunden (verifiziert: `curl --max-time 60` beantwortete sie in 10,5s), lang
  genug, um übliche Test-Client-Timeouts reißen zu lassen. Behoben durch einmaliges Aufräumen
  (`POST /folders/{id}/trash` bzw. `DELETE /documents/{id}` für alle 142 Root-Objekte, ausnahmslos
  an Test-Actor-Namen wie `webdav-test-*`/`cmis-test-*`/`connector-sdk-tests`/`migration-tests`
  erkennbar) — reduzierte dieselbe Anfrage auf 76ms. Keine Code-Änderung nötig, aber ein
  dokumentierter Beleg dafür, dass `list_children()`s "bewusst in Kauf genommener" O(Dokumente)-
  Overhead (siehe SDK-Docstring) bei einer über viele Sessions hinweg ungeräumten Wurzel real
  spürbar wird.
- **Bewusste Grenze: keine GUI-Client-Verifikation** — getestet über direkte HTTP-Aufrufe im
  rohen Browser-Binding-Wire-Format (kein Mocking), nicht über einen echten CMIS-Desktop-/
  Office-Client (keiner in dieser Umgebung verfügbar) — gleiche, bereits bei `webdav-connector`
  dokumentierte Grenze.
- **Bewusste Grenze: inhaltsloses Checkin nicht möglich** — `document-service`s Versions-Endpunkt
  verlangt je Version zwingend eine Datei, ein reiner Metadaten-/Kommentar-Checkin ohne
  Inhaltsänderung wird mit `invalidArgument` (400) abgelehnt statt eines künstlichen "leeren"
  Uploads.
- **Präzedenzfall**: ein künftiger dritter Connector, der ebenfalls kein Bibliotheks-Äquivalent
  vorfindet, sollte denselben Weg gehen — Spezifikation direkt lesen (nicht raten), Umfang bewusst
  auf die real vorhandenen DMS-Konzepte begrenzen, Lücken ehrlich als "bewusste Grenze"
  dokumentieren statt sie zu verschweigen oder Fantasie-Verhalten zu bauen.
