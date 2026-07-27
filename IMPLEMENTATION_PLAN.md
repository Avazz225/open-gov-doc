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
| BPMN-Engine | `SpiffWorkflow` + `bpmn-js-spiffworkflow` — **Lizenz-Check LGPLv3 vor P6-S1 einplanen** (13, offener Punkt) |
| OCR | PaddleOCR (Standard) + Tesseract (`pytesseract`, Alternative) (3.9) |
| Signatur | `pyHanko` (PAdES) (3.10) |
| Frontend | React/Next.js, primär Client-Side-Rendering (8); `bpmn-js` für den Process Designer (P6-S6) |
| Monitoring | Prometheus-Exposition + Grafana-Templates + CheckMK-Anbindung (10.1) |
| Containerisierung | Docker + `docker-compose` lokal; Kubernetes-Readiness erst ab P10 relevant |

## Session-Roadmap

Jede Zeile = eine Session (Konzept-Referenz in Klammern). `P4-S3` markiert den ersten großen Meilenstein (lauffähiges Kern-DMS).

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

### Phase 6 — Workflow & Vorgänge
| Session | Deliverable |
|---|---|
| P6-S1 | Workflow Engine Grundgerüst: SpiffWorkflow, BPMN-Import/-Ausführung, Manual/Automatic Tasks (7.1) — Lizenz-Check LGPLv3 zuerst |
| P6-S2 | SLA-Zeitüberwachung je Schritt (Timer/Boundary Events) + Notification Service |
| P6-S3 | Case Service: Umlaufmappen, dynamische Referenz + Abschluss-Snapshot, prozessspez. Bearbeitungskopien (2.3) |
| P6-S4 | Generischer Vier-Augen-Approval-Mechanismus + Superuser Break-Glass + Not-Shutdown (4.3/4.6/4.8) |
| P6-S5 | Signature Service: pyHanko/PAdES, SES/AES/QES, Signature-Task-Typ (3.10) |
| P6-S6 | **Process-Designer-UI** (eigenständige Frontend-Anwendung, **nicht** Teil der Admin-UI, siehe 7.1/8): grafische BPMN-2.0-Modellierung über `bpmn-js` gegen die Workflow Engine aus P6-S1, Import/Export von BPMN-XML, Validierung referenzierter Objekttypen/Ordnerziele/Instanz-Ziele beim Import |

### Phase 7 — Compliance & Aussonderung
| Session | Deliverable |
|---|---|
| P7-S1 | Aufbewahrung/Legal Hold + Zwangslöschung inkl. Löschregister (5.2/5.2a) |
| P7-S2 | Reporting Service (Standardberichte) + Forensik-Trace (5.4) |
| P7-S3 | Aussonderung & Langzeitarchivierung: PDF/A, XDOMEA, optionale KDBX-Schlüsselverwaltung (5.6) |

### Phase 8 — Diagnose-Werkzeuge
| Session | Deliverable |
|---|---|
| P8-S1 | Query & Trace Service: Lesezugriff, pglast/libpg_query-Erweiterung (6.1) |
| P8-S2 | Manipulationsmodus + alle Sicherungsstufen (Dry-Run, Vier-Augen, kritische Tabellen) |
| P8-S3 | CLI-Tool (6.2) |

### Phase 9 — Lizenzsystem
| Session | Deliverable |
|---|---|
| P9-S1 | License Service: Dimensionen, signierte Lizenzdatei, Nutzungsprüfung (9.1/9.2) |
| P9-S2 | Lizenzvermittlung über Registry, Demo-Modus/Sperrverhalten, Admin-UI-Integration (9.3) |

### Phase 10 — Orchestrierung & Rolling Updates
| Session | Deliverable |
|---|---|
| P10-S1 | Plugin Orchestration Service Grundgerüst: Manifest-Format, Cold-Start-Platzierung (3.8) |
| P10-S2 | Zeitprofil-bewusste Platzierung + FFD-Fallback + Plattform-Scheduler-Erkennung |
| P10-S3 | Rolling Updates: Drain-Mechanismus, Expand/Contract bei Schema-Änderungen (10.5) |

### Phase 11 — Monitoring & Backup/Restore
| Session | Deliverable |
|---|---|
| P11-S1 | Sensor-Konzept + Monitoring Service, Prometheus-Exposition, Sensor-Registry (10.1) |
| P11-S2 | Grafana-Dashboard-Templates + CheckMK-Anbindung |
| P11-S3 | Backup/Restore Orchestrator: koordinierte Sicherung, Point-in-Time-Konsistenz (10.4) |
| P11-S4 | Löschabgleich nach Restore + automatisierte Restore-Tests |

### Phase 12 — Connectoren & Migration
| Session | Deliverable |
|---|---|
| P12-S1 | Connector-SDK + Referenz-Connector (CMIS oder WebDAV) (3.3) |
| P12-S2 | Migration/Transfer Service: Sperren→Kopieren→Verifizieren→Freigabe→Löschung, Dry-Run (7.2) |
| P12-S3 | Konfigurationsimport/-export Service (JSON, Schema-Versionierung) (7.3) |

### Phase 13 — Mehrfachinstallation & Föderation
| Session | Deliverable |
|---|---|
| P13-S1 | Mehrfachinstallations-Grundlagen: Isolation, Installations-ID (3a) |
| P13-S2 | Fleet-/Lizenz-Management-Service (übergeordnete Verwaltungsebene) |
| P13-S3 | Federation Hub Service Grundgerüst: Adressbuch, Schaltzentrale, Versionskompatibilität (7.4) |
| P13-S4 | Föderierte Workflow-Schritte + Ende-zu-Ende-Verschlüsselung |

### Phase 14 — Vergleich & Erweiterungs-Vorbereitung
| Session | Deliverable |
|---|---|
| P14-S1 | Delta-/Vergleichsfunktion zwischen Installationen, Ignore-Regex (7.5) |
| P14-S2 | Reviewer/Approval-UI + Migrations-Konsole (8) |
| P14-S3 | Backlog-Kandidaten aus 12.2 (ERP-Konnektoren, Mobile, KI) sauber als Plugin-Erweiterungspunkte vorbereiten (nicht implementieren) + finaler Repo-weiter Doku-/Struktur-Audit |

**47 Sessions insgesamt** (ursprünglich 43, seit P4-S3 um P4-S4/S5/S6 und P6-S6 ergänzt — direktes Nutzer-Feedback nach dem ersten echten Browser-Test des MVP, siehe `PROGRESS.md`), davon 12 bis zum MVP-Meilenstein (P4-S3).

## PROGRESS.md — Resume-Mechanismus

`dms/PROGRESS.md` wird als erste Amtshandlung in P0-S1 angelegt und ist der Einstiegspunkt für jede neue Session:
- Tabelle aller Sessions (aus diesem Plan übernommen) mit Status (offen/in Arbeit/fertig)
- "Zuletzt abgeschlossen" / "Nächste Session" oben, damit eine neue Session sofort weiß, wo sie ansetzt
- Abschnitt "Offene Entscheidungen" (z. B. Suche-Backend, SpiffWorkflow-Lizenz) mit aktuellem Stand
- Kurzes Änderungslog pro Session (1–2 Zeilen: was wurde gebaut, was ist der nächste logische Schritt)

## Verifikation je Session

- `docker-compose up` im betroffenen Bereich muss fehlerfrei starten.
- `pytest` je Service läuft grün (Unit + einfache Integrationstests gegen die Compose-Umgebung).
- Bei UI-Sessions (P4-S2, P4-S3, P4-S4, P4-S5, P4-S6, P6-S6, P14-S2): Dev-Server starten, Kernpfad (Login → Navigation → Upload/Freigabe je nach Session) manuell im Browser durchklicken, nicht nur Typecheck/Build. **Erfahrungswert aus P4-S2/S3**: Reine curl-basierte Verifikation reicht nicht aus — ein CORS-Bug (fehlende Preflight-Behandlung im Gateway) und Layout-/UX-Mängel wurden erst durch echtes Browser-Testen der Nutzerin/des Nutzers sichtbar. Steht in der jeweiligen Session-Umgebung kein Browser zur Verfügung, ist das explizit als Einschränkung zu kommunizieren statt stillschweigend nur curl-Checks als ausreichend zu behandeln.
- Ab P8 zusätzlich: CLI-Smoke-Test der jeweils neuen Funktionalität.
