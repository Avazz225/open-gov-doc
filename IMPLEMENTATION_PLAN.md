# Implementierungsplan: DMS-Konzept → lauffähiges System (Multi-Session-Roadmap)

## Context

`Business__DMS-Konzept.md` (in diesem Ordner) beschreibt ein sehr umfangreiches, verteiltes DMS (Microservices, Python/FastAPI-first, ~25 eigenständige Services über Auth, Storage, Workflow, Signatur, Föderation, Lizenzierung, Monitoring bis Backup/Restore). Das ist zu groß für eine einzelne Session — dieser Plan ist so angelegt, dass **künftige Claude-Code-Sessions ihn Schritt für Schritt abarbeiten können**, wobei das Repo dabei durchgehend sauber strukturiert und dokumentiert bleibt (nicht erst am Ende aufgeräumt wird) und `graphify` zur Navigation durch Konzept und wachsenden Code eingesetzt wird.

Geklärte Vorgaben:
- **Nur `dms/` ist relevant** — andere Ordner im übergeordneten Arbeitsverzeichnis gehören zu unabhängigen Projekten und liefern keine wiederverwendbaren Konventionen für diesen Stack.
- **Sequenzierung**: vertikaler MVP-Slice zuerst (Phasen 0–4 liefern ein lauffähiges Kern-DMS), danach schrittweise Breite.
- **Umfang**: vollständige Abdeckung aller ~25 Services von Anfang an geplant, inkl. Federation Hub, Fleet-Management, Delta-Vergleich — nicht nur grobes Backlog.
- **Repo-Wurzel**: `dms/` selbst wird das Repo (aktuell weder hier noch im übergeordneten Verzeichnis Git vorhanden) — Konzept, Plan und künftiger Code leben zusammen an einem Ort.

## Zielstruktur des Repos (`dms/`)

```
dms/
  Business__DMS-Konzept.md      # bestehende Spec, bleibt Referenz-Dokument (Quelle der Wahrheit)
  IMPLEMENTATION_PLAN.md        # dieser Plan (persistiert, wird bei Bedarf ergänzt/versioniert)
  PROGRESS.md                   # lebendiger Fortschritts-Tracker, JEDE Session aktualisiert ihn
  README.md                     # Projekt-Überblick, Monorepo-Karte, Quickstart (docker-compose up)
  CONTRIBUTING.md               # Konventionen: Branching, Commit-Stil, Service-Template, DoD
  CLAUDE.md                     # Projekt-Instruktionen inkl. graphify-Abschnitt (via `graphify claude install`)
  docs/
    adr/                        # Architecture Decision Records, eine Datei je Entscheidung
    services/                   # 1 Kurzdoku je Service (Verantwortung, API, Schema, Sensoren)
  services/
    registry-service/
    auth-service/
    document-service/
    ...                        # je Service: src/, tests/, Dockerfile, README.md, pyproject.toml
  libs/                         # geteilte Python-Pakete (kein In-Process-Kopplung der Fachlogik!)
    dms-common/                 # Settings, Logging, OpenTelemetry-Basis
    dms-db-base/                # SQLAlchemy-Async-Setup, Schema-pro-Service-Konvention
    dms-eventbus-client/        # Publish/Consume-Interface über NATS JetStream (austauschbar)
    dms-auth-client/            # OIDC/JWT-Validierung gegen Keycloak
  infra/
    docker-compose.yml          # lokale Dev-Umgebung: Postgres, NATS, Keycloak, MinIO
    k8s/                        # später, sobald relevant
  tools/
    cli/                        # das DMS-CLI-Tool (6.2)
  graphify-out/                 # generiert, siehe graphify-Plan unten
  .github/workflows/            # CI: Lint + Tests je Service
```

**Monorepo-Begründung**: Die Services sind architektonisch unabhängig deploybar, aber für die Entwicklungsphase ist ein Monorepo pragmatischer (geteilte Libs, ein `docker-compose up` für alles, konsistente Konventionen, ein Ort für `graphify`). Das widerspricht dem Konzept nicht — jeder Service bleibt ein eigenständig containerisierter Prozess mit eigenem Schema (3.1).

## Cross-Cutting: Definition of Done (gilt für JEDE Session)

Jede Session, die Code produziert, ist erst abgeschlossen, wenn:
1. Der betroffene Service/die Lib hat: `README.md`, Tests (`pytest`), `Dockerfile`, Eintrag in `infra/docker-compose.yml`, strukturiertes Logging.
2. `docs/services/<service>.md` existiert/ist aktuell (Verantwortung, Endpunkte, Schema, Events die er publiziert/konsumiert).
3. Nicht-triviale Architekturentscheidungen (z. B. Suche-Backend-Wahl in P5-S4) sind als kurzes ADR in `docs/adr/` festgehalten.
4. `PROGRESS.md` ist aktualisiert: erledigte Session abgehakt, nächste Session benannt, offene Fragen notiert.
5. Bei substantiellem Code-Zuwachs: `graphify <path> --update` (siehe unten) — nicht zwingend nach jeder Session, aber am Ende jeder Phase verpflichtend.
6. Tests laufen grün (`pytest` je Service, `docker-compose up` startet ohne Fehler).

Damit ist "sauber strukturiert und dokumentiert" kein Abschlussschritt, sondern in jede Session eingebaut.

## graphify-Einsatzplan

- **P0-S1 (sofort)**: `/graphify dms/Business__DMS-Konzept.md` — baut den ersten Wissensgraphen über das Konzept selbst. Ab dann: `graphify explain "<Thema>"` statt erneutem Volltext-Lesen der 1003 Zeilen, wenn eine spätere Session Details zu einem Abschnitt braucht.
- Sobald das Repo-Skeleton steht (Ende P0-S1): `graphify claude install` (schreibt einen `## graphify`-Abschnitt in `dms/CLAUDE.md`, damit künftige Sessions automatisch prüfen/aktualisieren, ohne dass `/graphify` manuell aufgerufen werden muss) und `graphify hook install` (Git-Post-Commit-Hook, aktualisiert den Graphen automatisch bei Code-Änderungen).
- **Am Ende jeder Phase** (nicht jeder Einzel-Session): `graphify dms/ --update` — inkrementell, da nur neue/geänderte Dateien erneut verarbeitet werden (Manifest-Diffing), bestehender Graph bleibt erhalten.
- **Zu Beginn jeder künftigen Session**, besonders ab Phase 5+, wenn der Code wächst: `graphify query "<Frage>"`, `graphify path "A" "B"` oder `graphify explain "<Service/Konzept>"` nutzen, um sich zu orientieren ("was hängt vom Document Service ab", "wo ist die OCR-Pipeline verdrahtet") statt manuell durch alle Services zu grep-en.
- Kein API-Key nötig für Code (reine AST-Extraktion); optionaler Gemini-Key nur relevant, falls später Konzeptdokumente/Bilder erneut semantisch tiefer ausgewertet werden sollen.

## Tech-Stack (aus Konzept 1a übernommen, als Referenz für jede Session)

| Bereich | Wahl |
|---|---|
| Sprache/Framework | Python 3.12, FastAPI, Pydantic v2 |
| Paket-/Dependency-Mgmt | `uv` + `pyproject.toml` je Service (schnell, moderner Default — niedriges Risiko, bei Bedarf in P0-S1 revidierbar) |
| DB-Zugriff | SQLAlchemy 2.0 async + `asyncpg`, Postgres, Schema pro Service (3.1) |
| Event-Bus | NATS JetStream (`nats-py`) als Default; Kafka-Alternative erst bei Bedarf (3.4) |
| Auth | OIDC via Keycloak, `Authlib`/`python-jose`, `python-keycloak` (4.4) |
| Storage-Backends | `aioboto3`/`boto3` (S3/MinIO), `azure-storage-blob`, lokales FS mit `fcntl` (3.6) |
| Query-Konsole | `pglast`/`libpg_query` (6.1) |
| BPMN-Engine | `SpiffWorkflow` + `bpmn-js-spiffworkflow` — LGPLv3 als unveränderte Dependency akzeptiert, siehe [ADR 0018](docs/adr/0018-spiffworkflow-lgpl-license.md) (13) |
| OCR | PaddleOCR (Standard) + Tesseract (`pytesseract`, Alternative) (3.9) |
| Signatur | `pyHanko` (PAdES) (3.10) |
| Frontend | React/Next.js, primär Client-Side-Rendering (8); `bpmn-js` für den Process Designer (P6-S8, "bpmn.io License" akzeptiert, siehe [ADR 0021](docs/adr/0021-bpmn-io-license-watermark.md); bewusst ohne `bpmn-js-spiffworkflow`, siehe [ADR 0026](docs/adr/0026-process-designer-bpmn-js-without-spiffworkflow-addon.md)) |
| Monitoring | Prometheus-Exposition + Grafana-Templates + CheckMK-Anbindung (10.1) |
| Containerisierung | Docker + `docker-compose` lokal; Kubernetes-Readiness erst ab P10 relevant |

## Session-Roadmap

Jede Zeile = eine Session (Konzept-Referenz in Klammern). `P4-S3` markiert den ersten großen Meilenstein (lauffähiges Kern-DMS).

**Ab Phase 7** beginnt jede Phase zusätzlich mit einer eigenen Kickoff-Planungssession `PN-S0` (Entscheidung im Rahmen der Roadmap-Vorausplanung nach P6-S2, siehe `PROGRESS.md`): Konzept-Abschnitte der Phase gegen den dann aktuellen Code-/Bibliotheksstand verifizieren, externe Abhängigkeiten/Lizenzen klären (ADR bei Bedarf, analog ADR 0018/0021), Session-Zuschnitt innerhalb der Phase bei Bedarf anpassen (analog der Phase-6-Restrukturierung unten), Recherche-Notizen direkt in diesem Dokument und in `PROGRESS.md` ergänzen — kein Implementierungscode. Vermeidet, dass Detailrecherche für weit entfernte Phasen schon jetzt (und damit bis zur tatsächlichen Umsetzung ggf. veraltet) gemacht wird, macht die nötige Vorarbeit aber explizit statt implizit. Phase 6 selbst bekam kein eigenes `P6-S0`, da diese Vorausplanungsrunde nach P6-S2 rückwirkend genau diese Rolle für den Rest von Phase 6 übernommen hat.

### Phase 0 — Repo- & Tooling-Fundament
| Session | Deliverable |
|---|---|
| P0-S1 | `git init` in `dms/`, Monorepo-Skeleton (siehe Zielstruktur), `README.md`/`CONTRIBUTING.md`/`.gitignore`, `docker-compose.yml`-Grundgerüst (Postgres, NATS, Keycloak, MinIO — noch ohne Services), erster `graphify`-Lauf über das Konzept, `graphify claude install` + `graphify hook install` |
| P0-S2 | Shared Libs (`dms-common`, `dms-db-base`, `dms-eventbus-client`, `dms-auth-client`), Service-Template/Cookiecutter-Muster in `docs/service-template.md`, CI-Skeleton (`.github/workflows`: Lint via `ruff`, Tests via `pytest`) |

### Phase 1 — Control-Flow-Fundament
| Session | Deliverable |
|---|---|
| P1-S1 | Registry Service: Discovery (Registrierung, Heartbeat/Health, Routingtabelle) (3.2a) |
| P1-S2 | Event-Bus produktiv (NATS JetStream in Compose) + Audit Service Grundgerüst mit Hash-Chain (3.4/5.3) |

### Phase 2 — Auth & Permissions
| Session | Deliverable |
|---|---|
| P2-S1 | Keycloak in Compose + Auth Service als OIDC-Broker (4.4) |
| P2-S2 | Permission Service: RBAC, Ordner-Vererbung, ereignisgetriebener Rechte-Cache (4.1) |

### Phase 3 — Storage & Dokumenten-Kern
| Session | Deliverable |
|---|---|
| P3-S1 | Storage Service + Backend-Plugin-Interface, zunächst Local-FS + S3/MinIO (3.6) |
| P3-S2 | Document Service: CRUD, dauerhafte Versionierung, Locking inkl. Force-Unlock/Konfliktkopie (2.1/2.1a/4.2) |
| P3-S3 | Folder Service + Object-Type Service + Constraint Engine (2.1/2.2/4.5) |
| P3-S4 | Storage-Redundanz (Quorum/async Replikation, Fixity-Checks) + Bereichssperren (3.6/4.7) |

### Phase 4 — Vertikalen Slice abschließen (MVP)
| Session | Deliverable |
|---|---|
| P4-S1 | API-Gateway/BFF: Routing über Registry, Auth-Validierung, Rate Limiting (3.5) |
| P4-S2 | User-UI Grundgerüst: Login, Navigation, Upload/Download, Vorschau-Stub (8) |
| P4-S3 | Admin-UI Grundgerüst (Nutzer/Rollen, Objekttyp-Editor, Registry-Übersicht) + **Doku-/ADR-Pass**: Architekturdiagramm des bisherigen Stands, offene Entscheidungen konsolidiert → **Meilenstein: lauffähiges Kern-DMS** |
| P4-S4 | **User-UI v2** (Nutzer-Feedback nach erstem echten Browser-Test von P4-S2/S3, 8): dreigeteilter Hauptbereich (Explorer mit Dokumenttabs oben links, Metadaten-Panel unten links, Vorschau rechts, alle drei über Tab-Auswahl synchronisiert und resizable), Ordner-CRUD (bisher nur Navigation), Dokumentmetadaten anzeigen/bearbeiten (Attribute gemäß Objekttyp, 2.2 — braucht vermutlich einen neuen `PATCH`-Endpunkt am Document Service, siehe dessen "Offene Punkte"), linke iconbasierte Navigationsleiste für Cross-Cutting-Funktionen außerhalb des Hauptbereichs |
| P4-S5 | **Admin-UI v2** (dasselbe Nutzer-Feedback, 8): Dashboard-Layout mit linker, ausklapp-/gruppierbarer Navigationsseitenleiste statt Top-Nav-Links; Unterstützung mehrerer Installationen aus einer Admin-UI heraus (Installationsliste mit je eigenem Gateway-Endpunkt/eigener Sitzung, Wechsel ohne erneute Anmeldung solange die Sitzung gilt, siehe 3a/8) |
| P4-S6 | **Cross-UI Theming**: Hell/Dunkel/Hoher-Kontrast/Automatisch für beide bestehenden Frontend-Apps (8), Präferenz im Nutzerprofil statt nur lokal gespeichert und geräteübergreifend wirksam — Speicherort vermutlich ein Keycloak-User-Attribut über die bereits vorhandene Admin-API-Anbindung des Auth Service (P4-S3), damit kein neuer Persistenz-Baustein nötig wird; endgültige Technik-Entscheidung ist Teil dieser Session |

### Phase 5 — Verarbeitung: Scan, Rendering, OCR, Suche

Vor Beginn dieser Phase (auf Nutzerwunsch) gegen die Spec vertieft geplant, analog zum Detailgrad, den P4-S4/S5/S6 erst nachträglich durch echtes Nutzer-Feedback bekamen — hier vorweggenommen, indem die Konzept-Abschnitte 10.3/2.4/3.9/3.7 vorab genau gelesen wurden. Offene Architekturfragen (Suche-Backend) werden bewusst **nicht** vorab entschieden, sondern bleiben wie immer eine ADR-Entscheidung innerhalb der jeweiligen Session (Definition of Done, s. u.).

| Session | Deliverable |
|---|---|
| P5-S1 | **Virus-Scan Service** (10.3): neuer Service inkl. Quarantäne-Zustand, Engine austauschbar über dasselbe Plugin-Prinzip wie Storage-Backends/OCR-Engines (3.3/3.8) — die Spec nennt keine konkrete Engine (z. B. ClamAV als naheliegende Open-Source-Wahl). **Kernfrage, als ADR in der Session festzuhalten**: 10.3 verlangt "Virenscan verpflichtend vor Freigabe eines Uploads", der bestehende Upload-Pfad (Document Service `POST /documents`/`POST /documents/{id}/versions`) macht Inhalte aber sofort über `GET /documents/{id}/content` abrufbar, bevor ein asynchroner Event-Konsument reagieren könnte — ein reiner Konsum von `document.version.created` würde das Freigabe-Versprechen verletzen. Zu entscheiden: synchroner Scan-Aufruf direkt im Upload-Pfad des Document Service vor Festschreiben der Version, oder ein neuer Gating-Zustand (`scan_status`: pending/clean/infected) an `document_version`, der `GET .../content` und jede nachgelagerte Verarbeitung (Rendering/OCR, P5-S2/S3) bis zum Ergebnis blockiert. Bei Fund: Quarantäne statt automatischem Löschen (Nachvollziehbarkeit/Beweiswert), Audit-Ereignis, Benachrichtigung des Uploaders. Eigenes Postgres-Schema, Events, Selbstregistrierung wie bei jedem Service. |
| P5-S2 | **Rendering/Preview Service** (3.7/2.4): Thumbnails, PDF/A-Konvertierung, Wasserzeichen sowie **Ersatzdarstellungen** als eigener Schwerpunkt — anders als der Name "Preview" nahelegt, sind Ersatzdarstellungen laut 2.4 kein flüchtiger Cache-Eintrag, sondern **eigenständige, dauerhaft persistierte, versionierte Objekte** (2.1a): Ausfallsicherheit darf nicht von der Funktionsfähigkeit desselben Renderers abhängen, den sie eigentlich absichern sollen — Persistenz also über den Storage Service, nicht Redis/In-Memory. Erzeugung regelbasiert/konfigurierbar je Quellformat (z. B. ".docx → immer .txt-Ersatzdarstellung", ".pptx → .pdf") und selbst plugin-/service-basiert (3.3/3.8). Dockt nach dem Scan-Gating aus P5-S1 an `document.version.created` an. **Sessions-Reihenfolge weicht bewusst von der Konzept-Pipeline ab**: 3.9 sieht OCR *vor* Rendering vor, hier kommt OCR aber erst in P5-S3 — die textbasierte Ersatzdarstellung für bildbasierte/gescannte Dokumente (die laut 3.9 auf dem OCR-Textlayer aufbaut) kann diese Session daher noch nicht bedienen; bewusst auf Formate beschränken, die ohne OCR auskommen (Office-Formate, Video), OCR-gestützte Ersatzdarstellungen als Nachzieheffekt von P5-S3 ergänzen. Ersatzdarstellungen unterliegen denselben Berechtigungen wie das Original (2.4), sofern nicht abweichend konfiguriert. |
| P5-S3 | **OCR Service** (3.9): PaddleOCR als Standard-Engine (Apache-2.0, robuster bei Tabellen/mehrspaltigen/mehrsprachigen Layouts), Tesseract als ressourcenschonende Alternative — austauschbar über dasselbe Plugin-Interface-Prinzip wie Storage-Backends (3.3/3.6/3.8). Automatische Erkennung, ob ein Dokument überhaupt OCR braucht (vorhandener nutzbarer Textlayer ja/nein), statt es pauschal pro Dateityp zu erzwingen; Ergebnis (OCR-Textlayer) wird dauerhaft mit der jeweiligen Dokumentversion verknüpft gespeichert und speist sowohl den Search Service (P5-S4) als auch optional eine textbasierte Ersatzdarstellung (2.4, Nachzieheffekt für Dokumente aus P5-S2, die mangels OCR zuvor nicht bedient werden konnten). **BPMN-Abhängigkeit, bewusst nicht blockierend**: 3.9 sieht bei niedrigem Konfidenzwert optional eine manuelle Nachprüfung als BPMN-Prozessschritt vor — die Workflow Engine existiert aber erst ab P6-S1. Diese Session baut daher nur einen einfachen Zwischenzustand: Konfidenzwert + `needs_review`-Flag am OCR-Ergebnis, veröffentlicht als Event, ohne die Verfügbarkeit des Dokuments zu blockieren (anders als beim Virenscan). Die echte BPMN-Anbindung folgt später, vermutlich zusammen mit dem für P6-S4 vorgesehenen generischen Approval-Mechanismus. OCR-Ergebnisse sind wie jede Dokumentverarbeitung zu auditieren (5.3) — Audit Service muss um die neuen `ocr.>`-/`virus-scan.>`-/`rendering.>`-Streams erweitert werden. |
| P5-S4 | **Search Service** (3.7): Volltextindex + Facettensuche. **Backend-Entscheidung bewusst offen** (Postgres Full-Text-Search vs. dedizierter Suchindex) — wird nicht hier vorweggenommen, sondern als ADR innerhalb dieser Session getroffen. Facetten orientieren sich an vorhandener Struktur: Objekttyp, Ordner, Attribute gemäß Objekttyp-Schema (je Attributtyp `string`/`decimal`/`integer`/`boolean`/`date`/`reference` unterschiedliche Filterlogik, z. B. Bereichsfilter bei `date`/`decimal`), Ersteller, Erstellungsdatum, Status. Indexiert werden Metadaten (`document.created`/`document.version.created`/`document.metadata.updated`) sowie Volltext aus dem OCR-Textlayer (P5-S3) bzw. den textbasierten Ersatzdarstellungen (P5-S2), sobald verfügbar — Nachindexierung nötig, da OCR/Rendering zeitlich nach dem initialen Upload-Event abschließen. **Leicht zu übersehen**: Object-Type Service publiziert laut eigener Doku keine Events ("reiner Referenzdaten-Dienst") — Attributschema-Änderungen müssen synchron per HTTP abgefragt werden. **Berechtigungsfilterung**: Suchergebnisse müssen den seit P2-S2 bestehenden Rechte-Cache des Permission Service respektieren; da jeder Service nur sein eigenes Schema besitzt (3.1), läuft das über dessen API. Der bestehende `GET /check` prüft aber nur ein Principal/Resource/Permission-Tripel je Aufruf — für eine Trefferliste mit potenziell vielen Dokumenten ist ein neuer **Batch-Check-Endpoint** am Permission Service wahrscheinlich nötig und Teil dieser Session. |

### Phase 5b — Objektmodell-Erweiterung, Layout-Engine & Betriebs-Härtung

Nachträglich eingeschoben (Nutzerwunsch nach Abschluss von P5-S4, analog zum Präzedenzfall P4-S4/S5/S6, die ebenfalls erst nach dem ursprünglichen Phasenabschluss P4-S3 durch echtes Feedback ergänzt wurden) — erweitert Konzept-Abschnitte 2.2/2.2a/2.2b (erzwungene Objekt-Hierarchie, Icons, Formular-Layouts), 3.6 (Storage-Datenträger-Wechsel-Sensibilität, beliebig viele gleichartige Backend-Instanzen), 3.9 (OCR-Konfigurierbarkeit) und 8 (GUI-Objekttyp-/Layout-Designer, Explorer-Baumansicht). Touchiert bereits abgeschlossene Services aus Phase 3 (object-type-service, folder-service, document-service, storage-service) und Phase 5 (ocr-service) sowie beide bestehenden Frontends — bewusst als eigene Phase zwischen 5 und 6 statt als Rückbau/Umnummerierung der bereits abgeschlossenen Phasen (gleiches Präzedenzmuster wie Konzept-Abschnitt "3a", der ebenfalls nachträglich zwischen 3 und 4 eingefügt wurde, statt alles nachfolgend umzunummerieren).

| Session | Deliverable |
|---|---|
| P5b-S1 | **Objekttyp-Hierarchie (`allowedParentTypes`) + Klassen-Icons** (2.2/2.2a): Schema-Erweiterung im object-type-service (`allowedParentTypes: string[]`, Sonderwert `"$ROOT"`; `icon`-Feld für Ordnerklassen), Durchsetzung als neue Constraint-Prüfung bei Anlage *und* Verschieben eines Ordners/Dokuments (document-service/folder-service rufen wie bei den übrigen Constraints die Regel-Engine des object-type-service auf) — beides bereits über den bestehenden JSON-Ex-/Import (kein neuer Ex-/Import-Mechanismus nötig, reines Schema-Feld). ADR nötig für die Frage, ob `allowedParentTypes`-Änderungen rückwirkend gegen bestehende Ablagen geprüft werden (13, offener Punkt). |
| P5b-S2 | **Formular-Layout-Datenmodell** (2.2b): neues, generisches Layout-JSON-Format (Zeilen/Spalten-Grid, Responsive-Breakpoint-Regel) im object-type-service, gespeichert je Objekttyp und Verwendungszweck (Anzeige/Suche/Upload) — API zum Lesen/Schreiben eines Layouts, automatische "Smart Layout"-Generierung aus der Attributliste als Default, bevor P5b-S3 einen Editor dafür bekommt. |
| P5b-S3 | **Admin-UI: GUI-Objekttyp-Editor + Layout-Designer** (8): ersetzt den JSON-Freitext-Objekttyp-Editor aus P4-S3 durch einen geführten Formular-Assistenten (Attribute auswählen, Anzeigenamen vergeben, Pflichtfelder markieren, `allowedParentTypes`/`icon` auswählen) plus einen eigenen Layout-Designer-Bereich zum Nachjustieren der generierten Layouts (Anzeige/Suche/Upload je Objekttyp, aus P5b-S2). |
| P5b-S4 | **User-UI: Baumansicht, Klassen-Icons, layoutgesteuerte Formulare** (8): `ExplorerPane` bekommt eine nutzerseitig umschaltbare Baum-/Listenansicht sowie die Anzeige des Klassen-Icons vor jedem Namen; `MetadataPanel` (P4-S4), `SearchPane` (P5-S4) und der Upload-Dialog (P4-S2) werden von fest verdrahteten Formularen auf layoutgesteuertes Rendering (P5b-S2) umgestellt. |
| P5b-S5 | **OCR-Konfigurierbarkeit** (3.9): `ocr-service`/Admin-UI erhalten `ocrEnabled` (bei `false` muss der Service nicht deployt werden — Docker-Compose-Profil-Opt-out), eine konfigurierbare maximale Wortobergrenze (Dokumente darüber überspringen OCR) und eine konfigurierbare Verarbeitungs-Batch-Size — Retrofit von P5-S3. |
| P5b-S6 | **Storage Service: Datenträger-Identität & Redundanz-Wächter** (3.6): Index-/Identitätsdatei mit Geräte-ID je Backend-Instanz, Startverweigerung bei fehlender/abweichender Identität, Admin-Override ("Start erlaubt, wenn mindestens ein Backend nachweislich unverändert ist") inkl. zwingender Hintergrund-Replikation zur Wiederherstellung der Redundanz danach; außerdem explizite Unterstützung beliebig vieler gleichartiger Backend-Instanzen im selben Ziel-Set (z. B. 2× S3 + 1× NFS) — Retrofit von P3-S1/P3-S4. |

### Phase 5c — Konsolidierung offener Punkte (Betriebs-Härtung II)

Nachträglich eingeschoben nach Abschluss von Phase 5b, gleiches Präzedenzmuster wie die Einschiebung von Phase 5b selbst (siehe oben) — diesmal kein Nutzer-Feedback aus echter UI-Nutzung, sondern eine bewusste Konsolidierungsrunde über die in `PROGRESS.md` "Offene Entscheidungen" angesammelten Punkte, bevor Phase 6 beginnt. Nur die beiden Punkte, für die tatsächlich eine baldige Umsetzung entschieden wurde; die übrigen offenen Punkte bleiben bewusst Backlog (siehe `PROGRESS.md`).

| Session | Deliverable |
|---|---|
| P5c-S1 | **Test-DB-Isolationslücke schließen** (Tooling, kein Konzept-Bezug): Jede Service-`conftest.py` erzwingt jetzt `DMS_POSTGRES_DSN = TEST_POSTGRES_DSN` beim Laden, statt beide Variablen unabhängig dem Environment zu überlassen — schließt die Lücke, die zweimal echten Schaden verursacht hat (P5-S2 Datenverlust, P5b-S6 Live-DB-Leck), weil `TestClient(app)`-basierte Tests bislang unbemerkt `DMS_POSTGRES_DSN` (Live-Default) statt `TEST_POSTGRES_DSN` lasen. Verifiziert per echtem Testlauf gegen eine isolierte Wegwerf-Datenbank bei nur gesetztem `TEST_POSTGRES_DSN` (bewusst `DMS_POSTGRES_DSN` aus der Shell entfernt, um den ursprünglichen Fehlerfall zu reproduzieren) — 76/76 Tests grün, Live-`dms`-Datenbank nachweislich unverändert. |
| P5c-S2 | **Storage: Rebalancing bei Ziel-Set-Änderung + Gerätewechsel-Korrekturmechanismus** (3.6, ADR 0017-Folgepunkte): Ein neu zum Ziel-Set hinzugefügtes Backend bekommt bislang keine automatische Kopie bestehender Objekte (Rebalancing); ein beabsichtigter, legitimer Gerätewechsel erfordert aktuell eine manuelle Korrektur direkt in der `backend_identity`-Tabelle statt eines API-Endpunkts. Beides war seit ADR 0004 bzw. ADR 0017 als Backlog-Punkt vertagt (ursprünglich frühestens Phase 10/Plugin Orchestration Service), auf Nutzerentscheidung vorgezogen. |

### Phase 5d — Content-Type-Governance & Upload-/Vorschau-UX

Nachträglich eingeschoben nach Abschluss von Phase 5c, gleiches Präzedenzmuster wie Phase 5b (echtes Nutzer-Feedback aus tatsächlicher Nutzung, diesmal zu Vorschau-Lücken, OCR-Kostenkontrolle, Format-Governance und Upload-UX). Zwei vorab geklärte Design-Entscheidungen: OCR-Einschränkung koppelt an den **Dateityp/MIME** (nicht an den Objekttyp) — dieselbe Konfigurationsachse wie die neue Format-Whitelist, keine neue Verbindung ocr-service↔object-type-service nötig; Text-Vorschau (`text/plain`, `application/json`, ...) erfolgt **clientseitig direkt** (User-UI rendert den Originalinhalt in einem `<pre>`-Block) statt über einen neuen rendering-service-Renderer — kein Ersatzdarstellungs-Overhead für bereits textbasierte Inhalte.

| Session | Deliverable |
|---|---|
| P5d-S1 | **Document Service: Content-Type-Erkennung + konfigurierbare Format-Whitelist** (3.1/3.6): `DocumentVersion.content_type` wird serverseitig aus dem tatsächlichen Dateiinhalt ermittelt (Sniffing, nicht der ungeprüft vom Browser gesendete `file.content_type`-Header) — behebt, dass z. B. `.txt`/`.json` je nach Browser/OS mit generischem oder falschem Content-Type ankommen. Neue, DB-gestützte, Admin-UI-editierbare Konfiguration erlaubter Content-Types (gleiches Muster wie `OcrConfig`/`GuardConfig`) — Upload eines nicht gelisteten Formats wird mit `400` abgelehnt statt stillschweigend akzeptiert. **OCR Service** bekommt dieselbe Konfigurationsachse gespiegelt: eine zusätzliche, admin-editierbare Content-Type-**Positivliste** (`OcrConfig`-Erweiterung, `allowed_content_types`), die zusätzlich zur bestehenden technischen `select_engine()`-Logik greift — leer = keine Einschränkung, sonst löst nur ein gelisteter Content-Type tatsächlich OCR aus, obwohl `select_engine()` ggf. weitere technisch unterstützen würde. |
| P5d-S2 | **User-UI: Upload als Pop-up mit Drag & Drop + Text-Vorschau** (8): `UploadForm` wird von einem inline in `ExplorerPane` eingeblendeten Formular zu einem echten Modal-Dialog (Overlay), der zusätzlich Drag-and-Drop von Dateien auf die geöffnete Fläche entgegennimmt. `PreviewPane` bekommt eine clientseitige Direktanzeige für `text/*`-Content-Types (sowie `application/json` u. ä.) — kein neuer Renderer, kein Rendering-Service-Umweg. |

### Phase 5e — Kennzeichengenerator (Aktenzeichen)

Nachträglich eingeschoben nach Abschluss von Phase 5d, gleiches Präzedenzmuster wie 5b/5c/5d (Nutzerwunsch, diesmal während der P5d-S2-Planung geäußert). Drei vorab per Rückfrage geklärte Design-Entscheidungen: die laufende Nummer im Format-String setzt sich **pro Jahr** zurück (je Objekttyp ein eigener Zähler `{objekttyp_id, jahr}`); der Anzeigeort vor dem Dateinamen ist ein **einziger globaler Schalter mit optionalem Override je Dokumentenart** (kein Katalog mehrerer unabhängiger Anzeigepunkte); eine nachträgliche Änderung des generierten Kennzeichens ist nur privilegierten Nutzern erlaubt, für alle anderen ist es rein lesbar — dafür wird **erstmals im gesamten System** der vom Gateway bereits injizierte, bisher von keinem Service ausgewertete `X-DMS-Roles`-Header gelesen (neue Keycloak-Realm-Rolle `dms-admin`, im Auth-Service-Bootstrap idempotent angelegt).

| Session | Deliverable |
|---|---|
| P5e-S1 | **Object-Type Service: Kennzeichengenerator-Konfiguration + atomarer Zähler** (2.2): `ObjectType` bekommt zwei neue, nur für `applies_to="document"` relevante Felder — `kennzeichen_format` (Format-String mit Platzhaltern `{YYYY}`/`{YY}`/`{MM}`/`{DD}`/`{Laufende_Nummer}`, `null` = kein Generator) und `kennzeichen_display_override` (Tri-State `null`/`true`/`false` — überschreibt bei gesetztem Wert den globalen Anzeige-Standard aus P5e-S2 für diese Dokumentenart). Neue Tabelle `object_type_sequence` (`object_type_id`, `jahr`, `naechste_nummer`) für einen je Objekttyp+Jahr eigenständigen, gegen Nebenläufigkeit abgesicherten Zähler. Neuer Endpunkt `POST /object-types/{id}/next-kennzeichen` — atomarer Inkrement+Format-Aufruf, liefert den fertig gerenderten String (z. B. `2026-001`); `404`, wenn kein Format konfiguriert ist. |
| P5e-S2 | **Document Service: Kennzeichen-Vergabe bei Anlage + privilegierte Änderung** (2.2/4.4): `POST /documents` ruft bei konfiguriertem Generator serverseitig `POST /object-types/{id}/next-kennzeichen` auf und schreibt das Ergebnis unter dem reservierten Attributschlüssel `Kennzeichen` in `attributes` — ein vom Client mitgesendeter Wert für diesen Schlüssel wird dabei verworfen. `PATCH /documents/{id}` lehnt eine Änderung an `attributes["Kennzeichen"]` mit `403` ab, außer der `X-DMS-Roles`-Header des aufrufenden Principals enthält die neue Realm-Rolle `dms-admin` (Settings-Feld `kennzeichen_admin_role`, Default `"dms-admin"`) — erste echte Rollenprüfung im gesamten System, bislang wertete kein Service diesen Header aus (siehe `PROGRESS.md` "Autorisierung"). **Auth Service**: `ensure_realm_and_client` legt die Rolle `dms-admin` idempotent im Realm an (analog zum bestehenden Theme-Attribut-Bootstrap), Zuweisung an konkrete Nutzer weiterhin über die bestehende Rollenverwaltung (`/users`, Admin-UI). |
| P5e-S3 | **Admin-UI + User-UI: Generator-Konfiguration und Anzeige** (2.2/8): `ObjectTypeEditor` bekommt (nur bei `applies_to="document"`) ein Format-String-Feld sowie den Tri-State-Anzeige-Override; neue globale Einstellung (gleiches Einzelzeilen-Konfigurationsmuster wie `OcrConfig`/`UploadConfig`) für den Standard-Anzeigeschalter "Kennzeichen vor Dateinamen anzeigen". User-UI (`ExplorerPane`-Liste, Tab-Titel in `DocumentWorkspace`) blendet `attributes["Kennzeichen"]` vor dem Dateinamen ein, wenn der aufgelöste Schalter (Objekttyp-Override falls gesetzt, sonst globaler Standard) aktiv ist; `MetadataPanel` zeigt das Feld nur für `dms-admin`-Rolleninhaber editierbar, sonst rein lesbar. |

### Phase 6 — Workflow & Vorgänge

**Rest der Phase nach P6-S2 vorausgeplant** (Nutzerwunsch, siehe `PROGRESS.md` "Roadmap-Vorausplanung nach P6-S2"): die ursprüngliche einzelne `P6-S4` ("Generischer Vier-Augen-Approval-Mechanismus + Superuser Break-Glass + Not-Shutdown") bündelte drei eigenständige Konzeptthemen (4.3/4.6/4.8) und zugleich den geplanten Nachhol-Termin für sechs bereits bewusst ungesicherte Stellen aus früheren Sessions (Force-Unlock, Bereichssperren, Nutzerverwaltung, Admin-UI, Workflow Service, Notification Service — siehe `PROGRESS.md` "Autorisierung") — zu viel für eine Session. Aufgeteilt in P6-S4/S5/S6 (Zuordnung der sechs Alt-Fälle nach thematischer Nähe, **provisorisch**, bei jeweiligem Sessionstart zu bestätigen). Die ursprünglichen P6-S5 (Signature Service) und P6-S6 (Process Designer) rücken entsprechend auf P6-S7/P6-S8; Referenzen auf die alten Nummern in bereits bestehenden Dokumenten wurden mit-aktualisiert. **Bei der P6-S8-Planfreigabe erneut erweitert**: eine neue Session `P6-S9` (Federation Hub Grundgerüst, vorgezogen von P13-S3/S4) wurde ergänzt, nachdem der Nutzer bei der Process-Designer-Planung installationsübergreifende externe Swimlanes/Handover verlangte — zu umfangreich für P6-S8 selbst, siehe `PROGRESS.md` "Process Designer / Prozess-Versionierung".

| Session | Deliverable |
|---|---|
| P6-S1 | Workflow Engine Grundgerüst: SpiffWorkflow, BPMN-Import/-Ausführung, Manual/Automatic Tasks (7.1) — Lizenzfrage bereits geklärt, siehe [ADR 0018](docs/adr/0018-spiffworkflow-lgpl-license.md) |
| P6-S2 | SLA-Zeitüberwachung je Schritt (Timer/Boundary Events) + Notification Service |
| P6-S3 | Case Service: Umlaufmappen, dynamische Referenz + Abschluss-Snapshot, prozessspez. Bearbeitungskopien (2.3). Vermutlich ein neuer, eigenständiger `case-service` (Konvention: eigener Microservice je fachlicher Domäne, wie workflow-service/notification-service) — Umlaufmappe ist ein RBAC-/constraint-fähiges Objekt mit eigenem Objekttyp und eigenem Workflow-Lebenszyklus (Abhängigkeit auf workflow-service aus P6-S1). Prozessspezifische Bearbeitungskopien (z. B. Schwärzungen für Akteneinsicht) sind laut 2.3 ein eigener Objekttyp mit Verweis auf Ursprungsdokument+Vorgang — vermutlich eher ein neuer Zweig im Document Service als im Case Service selbst, bei Sessionstart gegen den dann aktuellen document-service-Stand zu prüfen. **Cross-Phase-Abhängigkeit**: P15-S3 (Posteingang/Poststelle) baut später auf der hier entstehenden Umlaufmappen-API auf. |
| P6-S4 | Generischer Vier-Augen-Approval-Mechanismus (4.3) + Retrofit: Force-Unlock (Document Service) und Bereichssperren (Permission Service) — beide von 4.3 selbst als Beispiel genannt und seit P3-S2/P3-S4 bewusst ungated. *(Provisorische Retrofit-Zuordnung, siehe Phase-Vorbemerkung oben.)* |
| P6-S5 | Superuser Break-Glass + abgespeckte, domänengetrennte Admin-Rollen (4.6) + Retrofit: Auth-Service-Nutzerverwaltung (`/users`) und Admin-UI-Rollenprüfung hinter den neuen Domänenrollen (u. a. "Nutzer-/Rechteverwaltung") gaten — beide seit P4-S3 bewusst ungated. *(Provisorische Retrofit-Zuordnung, siehe Phase-Vorbemerkung oben.)* |
| P6-S6 | Not-Shutdown: systemweite Notfallsperre & Wartungsmodus (4.8, baut auf der Break-Glass-Aktivierung aus P6-S5 auf, da der Wartungsmodus nur darüber wieder verlassen werden kann) + Retrofit: Workflow-Service-Autorisierung (Instanzstart/Task-Abschluss/Script-Task-Ausführung, seit P6-S1 ungated) und Notification-Service-Aufrufautorisierung (`POST /notifications`, seit P6-S2 ungated, inkl. Empfänger-Auflösung ohne RBAC). *(Provisorische Retrofit-Zuordnung, siehe Phase-Vorbemerkung oben.)* |
| P6-S7 | Signature Service: pyHanko/PAdES, SES/AES/QES, Signature-Task-Typ (3.10). `pyHanko` ist MIT-lizenziert (kein ADR-Bedarf wie bei SpiffWorkflow/bpmn-js). Empfohlener Zuschnitt: Signature Service + austauschbare Signature-Provider-Connectoren (Plugin-Prinzip wie Storage-Backends/CMIS, 3.3) bauen, für dieses Grundgerüst nur SES/AES mit systeminternen/selbstsignierten Schlüsseln real umsetzen/testen — ein echter akkreditierter QTSP für QES braucht eine externe Geschäftsbeziehung und ist kein Automatisierungs-/Testfall dieser Session. *(Ehemals P6-S5.)* |
| P6-S8 | **Process-Designer-UI** (eigenständige Frontend-Anwendung, **nicht** Teil der Admin-UI, siehe 7.1/8): grafische BPMN-2.0-Modellierung über `bpmn-js` gegen die Workflow Engine aus P6-S1, Import/Export von BPMN-XML, Prozess-Versionierung (`name` als Prozessfamilien-Schlüssel, siehe [ADR 0027](docs/adr/0027-workflow-process-definition-versioning.md)). **Lizenzfrage bereits geklärt**: `bpmn-js` steht unter der "bpmn.io License" (freie kommerzielle Nutzung, aber fest einprogrammiertes, nicht entfernbares bpmn.io-Wasserzeichen auf jedem gerenderten Diagramm, keine Kauf-Option zum Entfernen gefunden) — akzeptiert wie ADR 0018 bei SpiffWorkflow, siehe [ADR 0021](docs/adr/0021-bpmn-io-license-watermark.md). **`bpmn-js-spiffworkflow` bewusst nicht verwendet** (seit 2022 nicht mehr auf npm veröffentlicht, Lizenz-Inkonsistenz zwischen npm-Metadaten und aktuellem GitHub-`package.json`), stattdessen ein eigener Properties-Panel-Provider für den Signature Task, siehe [ADR 0026](docs/adr/0026-process-designer-bpmn-js-without-spiffworkflow-addon.md). **Rückfrage bei Planfreigabe** (siehe `PROGRESS.md` "Process Designer / Prozess-Versionierung"): installationsübergreifende externe Swimlanes/Handover (Federation Hub, 7.4) sind bewusst **nicht** Teil dieser Session, dafür neue Session P6-S9 (vorgezogen von P13-S3). *(Ehemals P6-S6.)* |
| P6-S9 | ✅ **Federation Hub Service Grundgerüst** (7.4): neuer, bewusst installationsunabhängiger `federation-hub-service` (Adressbuch + Schaltzentrale, Versionskompatibilitätsprüfung), zwei neue Manual-Task-Sonderformen im Workflow Service (`taskType=federated`/`federated_return`, automatischer Dispatch, kein manuelles Abschließen), neue Properties-Panel-Gruppe im Process Designer (nur sichtbar wenn der Hub Installationen kennt — bewusst **keine** echte Swimlane-Bearbeitung, siehe `docs/services/process-designer.md`), neuer Benachrichtigungs-Consumer im Notification Service. **Vorgezogen von P13-S3** (Adressbuch/Schaltzentrale/Versionskompatibilität) **und in der Sache auch von P13-S4** (föderierte Workflow-Schritte), Nutzerwunsch bei der P6-S8-Planfreigabe, siehe `PROGRESS.md`. **Rückfrage bei Planfreigabe**: die **Ende-zu-Ende-Verschlüsselung wurde entgegen der ursprünglichen Roadmap-Annahme bereits jetzt umgesetzt** (nicht erst mit P13-S4) — jede Installation erzeugt ein eigenes Schlüsselpaar, der Hub signiert Zustellungen mit einem eigenen, separaten Schlüsselpaar statt eines geteilten Geheimnisses (siehe [ADR 0028](docs/adr/0028-federation-hub-trust-and-encryption-model.md)). Getestet per Selbst-Loopback (einzige verfügbare Installation übergibt einen Schritt an sich selbst über den Hub) — ein echter Zwei-Installationen-Test steht aus. Bleibt weiterhin **keine Multi-Installation** des Process Designer selbst. |

### Phase 7 — Compliance & Aussonderung

**P7-S0-Kickoff-Befund**: Retention/Legal-Hold/Löschregister (5.2/5.2a) starten von einem praktisch leeren Stand — `document-service` hat nur ein rohes `deleted_at`, keine Trash-Wiederherstellungsfrist, kein Retention-Feld an Objekttyp/Ordner, kein Legal-Hold-Konzept irgendwo im Code. `audit-service` (5.3-Grundlage) hat zwar bereits eine hash-verkettete, unveränderliche Event-Chain (`AuditEvent`, `prev_hash`/`hash`) und produzentenseitig meist ein `subject`-Feld für das betroffene Objekt, aber **keinen first-class Actor/User-Feld** (steckt ad hoc in `payload`) und **keine Filter-API** (`GET /events` kennt nur `limit`) — P7-S2 muss das für die Forensik-Trace (5.4b, "alle Aktionen von Nutzer X"/"alle Nutzer auf Dokument Y") nachrüsten, nicht nur ein neues Read-Modell obendrauf bauen. `storage-service` unterstützt bereits mehrere gleichzeitige Backends (ADR 0017) — gute Grundlage für 5.6/5.2a —, aber **kein Object-Lock/WORM/Governance-vs-Compliance-Mode** ist implementiert (5.1/5.2a brauchen das für die kontrollierte Zwangslöschungs-Ausnahme); das ist bisher in keiner Session eingeplant und muss als Vorarbeit in P7-S1 mit hinein. Für 5.6: `rendering-service`s `PdfArchiveRenderer` existiert, ist aber laut eigenem Docstring nur Best-Effort (kein ISO-19005-Nachweis, kein Ghostscript/veraPDF) — für eine "verpflichtende" Archivkonvertierung ggf. nachzuschärfen. Die Umlaufmappen-Abschluss-Snapshot-Logik (2.3, `case-service`) ist bereits fertig und direkt wiederverwendbar. Der Migrationsprozess (7.2), mit dem 5.6 den Aussonderungsworkflow als "technisch eng verwandt" beschreibt, **existiert noch nicht** — P7-S3 baut sein eigenes auditiertes/resumable Workflow-Muster über die vorhandene Workflow Engine, ohne ein 7.2-Vorbild kopieren zu können. XDOMEA/KDBX-Lizenzklärung siehe [ADR 0029](docs/adr/0029-aussonderung-xdomea-eigenimplementierung-kdbx-plugin.md): keine gepflegte XDOMEA-Bibliothek verfügbar (Eigenserialisierung gegen die offiziellen XDOMEA-3.0.0-XSD-Schemata via `lxml`), KDBX (`pykeepass`, GPL-3.0) wird **nicht** ins Standard-Image gebündelt, sondern als optionales, separat nachinstallierbares Plugin umgesetzt (Nutzerentscheidung bei Sessionstart).

| Session | Deliverable |
|---|---|
| P7-S0 | ✅ Phasen-Kickoff-Planung (kein Implementierungscode): Konzept-Abschnitte 5.2/5.2a/5.4/5.6 gegen den Code-/Bibliotheksstand verifiziert (Befund siehe Phasen-Vorbemerkung oben), XDOMEA-/KDBX-Lizenzfrage geklärt ([ADR 0029](docs/adr/0029-aussonderung-xdomea-eigenimplementierung-kdbx-plugin.md), Rückfrage bei Sessionstart: KDBX als optionales Plugin statt gebündelter GPL-3.0-Abhängigkeit), Session-Zuschnitt P7-S1/S2/S3 entsprechend präzisiert. |
| P7-S1 | Aufbewahrung/Legal Hold + Zwangslöschung inkl. Löschregister (5.2/5.2a). **Erweiterter Zuschnitt laut P7-S0-Befund**: schließt Retention-Feld an Objekttyp/Dokument, Trash-Wiederherstellungsfrist (bisher nur rohes `deleted_at`), Legal-Hold-Konzept (bisher nicht existent) sowie als Vorarbeit ein Object-Lock/WORM-Mechanismus im Storage Service (Governance- vs. Compliance-Mode, 5.1/5.2a) mit ein — bei Sessionstart zu prüfen, ob das noch in eine Session passt oder eine eigene Storage-Service-Erweiterung vorweg braucht. |
| P7-S2 | Reporting Service (Standardberichte) + Forensik-Trace (5.4). **Erweiterter Zuschnitt laut P7-S0-Befund**: Voraussetzung ist ein Retrofit des `audit-service`s — first-class Actor/User-Feld auf `AuditEvent` statt ad-hoc-`payload`-Konvention, plus eine Filter-API (Subjekt/Nutzer/Zeitraum/Aktionstyp), sonst ist die Forensik-Trace (5.4b) nicht sinnvoll baubar. |
| P7-S3 | Aussonderung & Langzeitarchivierung: PDF/A, XDOMEA, optionale KDBX-Schlüsselverwaltung (5.6). **Präzisiert laut ADR 0029**: XDOMEA-3.0.0-Serialisierung/-Validierung selbst gegen die offiziellen XSD-Schemata via `lxml`, kein Bibliotheks-Import. KDBX-Anbindung als optionales, nicht gebündeltes Plugin (eigene Schnittstelle im Kern, `pykeepass` nur in einem separat zu installierenden Erweiterungspaket). Ggf. nachzuschärfende `PdfArchiveRenderer`-Konformität für eine "verpflichtende" Archivkonvertierung bei Sessionstart prüfen. Kein 7.2-Vorbild verfügbar — eigenes auditiertes/resumable Workflow-Muster über die Workflow Engine. |

### Phase 8 — Diagnose-Werkzeuge
| Session | Deliverable |
|---|---|
| P8-S0 | Phasen-Kickoff-Planung (kein Implementierungscode): Konzept-Abschnitte 6.1/6.2 verifizieren, `pglast`/`libpg_query`-Bibliotheksstand + Lizenz prüfen, Session-Zuschnitt bei Bedarf anpassen |
| P8-S1 | Query & Trace Service: Lesezugriff, pglast/libpg_query-Erweiterung (6.1) |
| P8-S2 | Manipulationsmodus + alle Sicherungsstufen (Dry-Run, Vier-Augen, kritische Tabellen) |
| P8-S3 | CLI-Tool (6.2) |

### Phase 9 — Lizenzsystem
| Session | Deliverable |
|---|---|
| P9-S0 | Phasen-Kickoff-Planung (kein Implementierungscode): Konzept-Abschnitte 9.1/9.2/9.3 verifizieren, Signaturverfahren für die Lizenzdatei festlegen, Session-Zuschnitt bei Bedarf anpassen |
| P9-S1 | License Service: Dimensionen, signierte Lizenzdatei, Nutzungsprüfung (9.1/9.2) |
| P9-S2 | Lizenzvermittlung über Registry, Demo-Modus/Sperrverhalten, Admin-UI-Integration (9.3) |

### Phase 10 — Orchestrierung & Rolling Updates
| Session | Deliverable |
|---|---|
| P10-S0 | Phasen-Kickoff-Planung (kein Implementierungscode): Konzept-Abschnitte 3.8/10.5 verifizieren, Plattform-Scheduler-Landschaft (Docker Compose/Swarm/Kubernetes) gegen den dann aktuellen Deploy-Stand prüfen, Session-Zuschnitt bei Bedarf anpassen |
| P10-S1 | Plugin Orchestration Service Grundgerüst: Manifest-Format, Cold-Start-Platzierung (3.8) |
| P10-S2 | Zeitprofil-bewusste Platzierung + FFD-Fallback + Plattform-Scheduler-Erkennung |
| P10-S3 | Rolling Updates: Drain-Mechanismus, Expand/Contract bei Schema-Änderungen (10.5) |

### Phase 11 — Monitoring & Backup/Restore
| Session | Deliverable |
|---|---|
| P11-S0 | Phasen-Kickoff-Planung (kein Implementierungscode): Konzept-Abschnitte 10.1/10.4 verifizieren, Prometheus-/Grafana-/CheckMK-Anbindung gegen den dann aktuellen Infrastruktur-Stand prüfen, Session-Zuschnitt bei Bedarf anpassen |
| P11-S1 | Sensor-Konzept + Monitoring Service, Prometheus-Exposition, Sensor-Registry (10.1) |
| P11-S2 | Grafana-Dashboard-Templates + CheckMK-Anbindung |
| P11-S3 | Backup/Restore Orchestrator: koordinierte Sicherung, Point-in-Time-Konsistenz (10.4) |
| P11-S4 | Löschabgleich nach Restore + automatisierte Restore-Tests |

### Phase 12 — Connectoren & Migration
| Session | Deliverable |
|---|---|
| P12-S0 | Phasen-Kickoff-Planung (kein Implementierungscode): Konzept-Abschnitte 3.3/7.2/7.3 verifizieren, CMIS-vs-WebDAV-Referenz-Connector-Entscheidung vorbereiten, Session-Zuschnitt bei Bedarf anpassen |
| P12-S1 | Connector-SDK + Referenz-Connector (CMIS oder WebDAV) (3.3) |
| P12-S2 | Migration/Transfer Service: Sperren→Kopieren→Verifizieren→Freigabe→Löschung, Dry-Run (7.2) |
| P12-S3 | Konfigurationsimport/-export Service (JSON, Schema-Versionierung) (7.3) |

### Phase 13 — Mehrfachinstallation & Föderation
| Session | Deliverable |
|---|---|
| P13-S0 | Phasen-Kickoff-Planung (kein Implementierungscode): Konzept-Abschnitte 3a/7.4 verifizieren, Ende-zu-Ende-Verschlüsselungsverfahren für föderierte Workflow-Schritte vorbereiten, Session-Zuschnitt bei Bedarf anpassen |
| P13-S1 | Mehrfachinstallations-Grundlagen: Isolation, Installations-ID (3a) |
| P13-S2 | Fleet-/Lizenz-Management-Service (übergeordnete Verwaltungsebene) |
| P13-S3 | *(Vorgezogen nach P6-S9, Phase 6 — Federation Hub Service Grundgerüst inkl. externer Swimlanes/Handover wurde bereits dort umgesetzt, siehe `PROGRESS.md`.)* Verbleibend hier zu prüfen: Anpassung an die dann existierenden Mehrfachinstallations-/Fleet-Grundlagen aus P13-S1/S2 (Adressbuch/Versionskompatibilität ggf. erweitern), falls sich seit P6-S9 Lücken zeigen. |
| P13-S4 | *(Umfang reduziert nach P6-S9, Phase 6 — echte Ende-zu-Ende-Verschlüsselung zwischen Installationen wurde bereits dort umgesetzt, siehe ADR 0028 in `docs/services/federation-hub-service.md`.)* Verbleibend hier: Härtung des in P6-S9 bewusst einfach gehaltenen Vertrauensmodells auf Basis der dann existierenden Installations-ID-/PKI-Grundlagen aus P13-S1 — mTLS statt reiner API-Key-Authentifizierung, Schlüsselrotation/-Revocation für die föderierten Installationsschlüsselpaare, echte Installations-Identität statt selbst gewählter `installation_id`. |

### Phase 14 — Vergleich & Erweiterungs-Vorbereitung
| Session | Deliverable |
|---|---|
| P14-S0 | Phasen-Kickoff-Planung (kein Implementierungscode): Konzept-Abschnitte 7.5/8/12.2 verifizieren, Session-Zuschnitt bei Bedarf anpassen |
| P14-S1 | Delta-/Vergleichsfunktion zwischen Installationen, Ignore-Regex (7.5) |
| P14-S2 | Reviewer/Approval-UI + Migrations-Konsole (8) |
| P14-S3 | Backlog-Kandidaten aus 12.2 (ERP-Konnektoren, Mobile, KI) sauber als Plugin-Erweiterungspunkte vorbereiten (nicht implementieren) + finaler Repo-weiter Doku-/Struktur-Audit |

### Phase 15 — Bereichsstruktur: Sonderbereiche (2.5)

Nachträglich eingeschoben (Nutzerwunsch): neben der konfigurierbaren Dokument-Root (Akte→Aktenplan→Abteilung im Beispiel aus 2.5) sieht das Konzept mehrere vom System bereitgestellte **Sonderbereiche** vor, analog den Sonderordnern eines E-Mail-Programms. Bewusst **ans Ende der Roadmap gesetzt**, nicht an die entsprechende thematische Stelle (z. B. neben Phase 7 für Papierkorb/Aussonderung) einsortiert: mehrere Sessions bauen auf Services auf, die erst in späteren Phasen entstehen (Aussonderungs-Zugriff braucht Phase 7, Vorlagen bauen auf dem Config-Import/Export-Service aus Phase 12 auf, die föderierte Kontaktsuche auf dem Federation Hub aus Phase 13) - an dieser Stelle in der Roadmap sind alle Abhängigkeiten bereits erfüllt.

| Session | Deliverable |
|---|---|
| P15-S0 | Phasen-Kickoff-Planung (kein Implementierungscode): Konzept-Abschnitt 2.5 gegen den dann aktuellen Stand von Phase 7 (Aussonderung)/Phase 12 (Config-Import/Export)/Phase 13 (Federation Hub) verifizieren, da P15-S3/S5/S6 direkt darauf aufbauen, Session-Zuschnitt bei Bedarf anpassen |
| P15-S1 | **Papierkorb-Familie** (2.5/4.6): regulärer Papierkorb + persönlicher Papierkorb (Filteransicht "eigene Löschmarkierungen") + strukturell getrennter Verschlusssachen-Papierkorb (Kennzeichnung über Objekttyp/Attribut, 2.2). Neue domänengetrennte Admin-Rollen **Löschadministration** und **Löschadministration (Verschlusssachen)** (4.6) mit endgültigem Löschrecht ausschließlich aus dem jeweiligen Papierkorb - reguläre Nutzer können nur markieren (Soft-Delete, bereits vorhanden), nicht endgültig löschen. |
| P15-S2 | **Quarantäne-Bereich** (2.5/10.3): UI-Exposition der bereits bestehenden Virenscan-Quarantäne (storage-service `quarantine/{scan_id}`-Konvention, virus-scan-service) als eigener, rechtebeschränkter Bereich mit auditierten Aktionen "endgültig löschen" und "freigeben" (Fehlalarm-Fall). |
| P15-S3 | **Posteingang/Postausgang + Poststelle-Rolle** (2.5/3.3/7.1): technischer Empfang extern eingehender/ausgehender Korrespondenz (z. B. E-Mail-Connector nach dem Connector-Prinzip, 3.3), Sichtung/Zuordnung durch eine dedizierte Poststelle-Rolle in einen Aktenplan oder eine (neue/bestehende) Umlaufmappe (2.3), optional als BPMN-Zuordnungsschritt (7.1). |
| P15-S4 | **Kontakte** (2.5/4.4/7.4): Verzeichnis-UI auf Basis der bestehenden Nutzerverwaltung (lokal, immer verfügbar); optionale installationsübergreifende Kontaktsuche als Erweiterung des Federation Hub (7.4) - eigene, explizit opt-in konfigurierbare Fähigkeit, keine automatische Nebenwirkung der bestehenden Föderationsfunktion (Adressbuch des Hubs verzeichnet Installationen, nicht Personen). |
| P15-S5 | **Aussonderungs-Zugriffsbereich** (2.5/5.6/7.2): UI-Bereich, der bereits ausgesonderte, aber noch innerhalb der Übergangsfrist befindliche Akten/Umlaufmappen durchsuch-/einsehbar macht, statt nur indirekt über Audit-Trail-Verweise auffindbar zu sein. |
| P15-S6 | **Vorlagen** (2.5/7.3): Struktur-Vorlagen (z. B. Aktenplan-Rohbau) auf Basis des bestehenden JSON-Struktur-Ex-/Imports (Config Import/Export Service, 7.3) - Vorlage = benannter Struktur-Export im Vorlagen-Bereich, Anwendung = gezielter Struktur-Import unterhalb eines gewählten Zielorts. |

### Phase 16 — Flexibler User-UI-Arbeitsbereich (8)

Nachträglich eingeschoben (Nutzerwunsch): der bisherige feste Drei-Spalten-Explorer/Metadaten/Vorschau-Bereich der User-UI wird durch einen dockbaren, frei umbaubaren Arbeitsbereich ergänzt (Editor-Metapher analog Visual Studio Code) - die bisherige Anordnung bleibt Werksstandard, ist aber nicht mehr die einzige Möglichkeit.

| Session | Deliverable |
|---|---|
| P16-S0 | Phasen-Kickoff-Planung (kein Implementierungscode): Konzept-Abschnitt 8 (flexibler Arbeitsbereich) verifizieren, Docking-Layout-Bibliothek für React evaluieren, Session-Zuschnitt bei Bedarf anpassen |
| P16-S1 | **Dockbares Arbeitsbereich-Grundgerüst**: Panels (Explorer, Dokumenttabs, Vorschau, Metadaten) frei andockbar/verschiebbar/stapelbar, mehrere Dokumente gleichzeitig sicht- und anordenbar; Dokumenttabs künftig über der Vorschau statt über dem Explorer (neue Standardanordnung, 2.5/8), Explorer als eigenständiges Panel daneben. Layout-Präferenz je Gerät lokal gespeichert (nicht geräteübergreifend wie das Farbschema), mit "Zurücksetzen auf Standardanordnung". Bewusst **kein** Bestandteil dieser Session: freie CSS-/Themeanpassung durch Endnutzer (Konzept 8, bewusst zurückgestellt). |

**80 Sessions insgesamt** (ursprünglich 43, seit P4-S3 um P4-S4/S5/S6 und P6-S6 [jetzt P6-S8, siehe unten] ergänzt — direktes Nutzer-Feedback nach dem ersten echten Browser-Test des MVP; seit P5-S4 um Phase 5b (P5b-S1–S6) ergänzt — nachträglicher Ausbauwunsch nach Abschluss der Verarbeitungs-Phase; seit P5b-S6 um Phase 5c (P5c-S1/S2) ergänzt — Konsolidierungsrunde offener Punkte; seit P5c-S2 um Phase 5d (P5d-S1/S2) ergänzt — Nutzer-Feedback zu Vorschau-Lücken/OCR-Kostenkontrolle/Format-Governance/Upload-UX; seit P5d-S2 um Phase 5e (P5e-S1–S3) ergänzt — Nutzerwunsch nach einem konfigurierbaren Kennzeichengenerator (Aktenzeichen); seit P5e um Phase 15 (P15-S1–S6, Sonderbereiche/2.5) und Phase 16 (P16-S1, flexibler Arbeitsbereich) ergänzt — konzeptionelle Erweiterung um Sonderbereiche und ein VS-Code-artiges Arbeitsbereich-Layout; **seit P6-S2 um 12 weitere Sessions ergänzt** (Roadmap-Vorausplanung, siehe `PROGRESS.md` "Roadmap-Vorausplanung nach P6-S2"): die ursprüngliche P6-S4 wurde in P6-S4/S5/S6 aufgeteilt (+2, ehemalige Signature/Process-Designer-Sessions rücken auf P6-S7/S8), und jede Phase ab Phase 7 bekam eine neue Kickoff-Planungssession `PN-S0` vorangestellt (+10, siehe "Session-Roadmap"-Hinweis oben) — davon 12 bis zum MVP-Meilenstein (P4-S3); **seit P6-S8 um eine weitere Session ergänzt** (+1, `P6-S9` Federation Hub Grundgerüst, vorgezogen von P13-S3/S4 — Nutzerwunsch bei der P6-S8-Planfreigabe nach externen Swimlanes/Handover, siehe `PROGRESS.md`).

## PROGRESS.md — Resume-Mechanismus

`dms/PROGRESS.md` wird als erste Amtshandlung in P0-S1 angelegt und ist der Einstiegspunkt für jede neue Session:
- Tabelle aller Sessions (aus diesem Plan übernommen) mit Status (offen/in Arbeit/fertig)
- "Zuletzt abgeschlossen" / "Nächste Session" oben, damit eine neue Session sofort weiß, wo sie ansetzt
- Abschnitt "Offene Entscheidungen" (z. B. Suche-Backend, SpiffWorkflow-Lizenz) mit aktuellem Stand
- Kurzes Änderungslog pro Session (1–2 Zeilen: was wurde gebaut, was ist der nächste logische Schritt)

## Verifikation je Session

- `docker-compose up` im betroffenen Bereich muss fehlerfrei starten.
- `pytest` je Service läuft grün (Unit + einfache Integrationstests gegen die Compose-Umgebung).
- Bei UI-Sessions (P4-S2, P4-S3, P4-S4, P4-S5, P4-S6, P5b-S3, P5b-S4, P6-S8, P14-S2, P15-S1, P15-S3, P15-S4, P16-S1): Dev-Server starten, Kernpfad (Login → Navigation → Upload/Freigabe je nach Session) manuell im Browser durchklicken, nicht nur Typecheck/Build. **Erfahrungswert aus P4-S2/S3**: Reine curl-basierte Verifikation reicht nicht aus — ein CORS-Bug (fehlende Preflight-Behandlung im Gateway) und Layout-/UX-Mängel wurden erst durch echtes Browser-Testen der Nutzerin/des Nutzers sichtbar. Steht in der jeweiligen Session-Umgebung kein Browser zur Verfügung, ist das explizit als Einschränkung zu kommunizieren statt stillschweigend nur curl-Checks als ausreichend zu behandeln.
- Ab P8 zusätzlich: CLI-Smoke-Test der jeweils neuen Funktionalität.
