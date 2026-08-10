# libreoffice-addin

**Verantwortung:** LibreOffice/OpenOffice-**Writer**-Erweiterung (UNO-API, Python, `.oxt`-Paket) für native OG-Doc-Integration (3.3a): Öffnen/Speichern eines Dokuments direkt aus/in OG Doc, inline Metadatenbearbeitung, Workflow-Start/-Fortsetzung, zentrale rollenbasierte Vorlagenbibliothek. P14-S9, gleichwertiges Gegenstück zum Microsoft-Office-Add-in (P14-S8) — "gemeinsame Backend-Schnittstelle mit dem MS-Office-Add-in" (Roadmap-Wortlaut).

**Konzept-Referenz:** 3.3a, 3.3, 7.1, 2.2, 2.5
**Kein eigenes Postgres-Schema** — reine Client-Erweiterung, kein eigener Backend-Prozess. Spricht wie `apps/office-addin` **keinen einzigen neuen Endpunkt** an.
**ADR:** [0046 — Nur Writer, Dialog-Hub statt Sidebar, `loadComponentFromURL` statt In-Place-Ersatz](../adr/0046-libreoffice-addin-writer-only-dialog-hub-loadcomponent.md)

## Ort im Repo

`apps/libreoffice-addin/` — kein Next.js/Node, sondern ein `.oxt`-Erweiterungspaket (ZIP mit fester Struktur): `META-INF/manifest.xml`, `description.xml` (+ `description/`-Textdateien, `registration/icon.png`), `Addons.xcu` (Menüregistrierung), `python/` (die eigentliche Implementierung, reines Python, keine Drittanbieter-Pakete). `build.py` packt daraus `OgDocAddin.oxt`.

## Funktionsumfang

| Bereich | Umsetzung |
|---|---|
| **Aus OG Doc öffnen** | Dialog mit Suchfeld (`search-service`) + Ergebnisliste → Inhalt laden (`GET /documents/{id}/content`) → `Desktop.loadComponentFromURL()` öffnet ein NEUES Writer-Fenster mit dem Inhalt → Sperre versuchen (`POST /documents/{id}/lock`) → Verknüpfung in `UserDefinedProperties` persistieren. |
| **Neu aus Vorlage** | Dialog listet Dokumente aus dem Wurzelordner "Vorlagen" → Inhalt laden → `loadComponentFromURL(..., AsTemplate=True)` — LibreOffice' eigene "Neu aus Vorlage"-Ladeoption. Beim ersten Speichern (Titel-Dialog) `POST /documents` mit `derived_from_document_id`/`derived_from_version_number`. |
| **In OG Doc speichern** | `doc.storeToURL(temp_url, FilterName=...)` exportiert den aktuellen Bearbeitungsstand in eine temporäre Datei → `POST /documents/{id}/versions` (Check-in, `expected_base_version_number` wie jeder andere Client). |
| **Inline-Metadaten** | Dialog: Titel + ein Textfeld je Objekttyp-Attribut (`GET /object-types/{id}`) → `PATCH /documents/{id}`. |
| **Workflow starten/fortsetzen** | Dialog: `GET /instances?business_key={documentId}` + `GET /instances/{id}/tasks`, `POST .../complete`, `POST /process-definitions/{id}/instances` mit `business_key={documentId}`. |

Ein einziger Menüeintrag ("Extras > OG Doc öffnen...", `Addons.xcu`) öffnet einen "Hub"-Dialog mit kontextabhängigen Buttons (nicht angemeldet → nur "Anmelden"; angemeldet ohne Verknüpfung → "Öffnen"/"Neu aus Vorlage"; verknüpft → "Metadaten"/"Speichern"/"Workflow"/"Verknüpfung lösen") — derselbe Ein-Knopf-öffnet-alles-Gedanke wie der Ribbon-Button in `apps/office-addin` (P14-S8), hier als Dialog-Kette statt eines dauerhaften Web-Taskpanes (UNO kennt kein leichtgewichtiges Sidebar-Äquivalent in Python, siehe ADR 0046).

## Dokument-Verknüpfung: `UserDefinedProperties` statt Server-Zustand

Analog zu Office.js' `document.settings` (ADR 0045): `document.getDocumentProperties().UserDefinedProperties` speichert drei Werte direkt in der Datei selbst (ODF `meta.xml`/OOXML Core-Properties) — `ogdoc_document_id`, `ogdoc_version_number`, **`ogdoc_content_type`**. Der Content-Type wird bewusst mitgespeichert (nicht nur ID+Version): ein vor dem Live-Test dieser Session gefundener Bug hätte sonst beim Speichern IMMER nach ODF konvertiert, unabhängig vom ursprünglichen Format (z. B. DOCX) — siehe ADR 0046 "Begründung".

## Öffnen erzeugt ein NEUES Fenster (nicht In-Place)

Anders als Word-JS' `insertFileFromBase64(..., replace)` gibt es in der Writer-API keine vergleichbar robuste "ersetze das aktuelle Dokument"-Fähigkeit. Stattdessen: heruntergeladene Bytes → temporäre lokale Datei → `Desktop.loadComponentFromURL(url, "_blank", 0, props)` öffnet sie als echtes neues Fenster — idiomatischer für ein Desktop-Programm, siehe ADR 0046. Nachfolgende Aktionen (Metadaten/Speichern/Workflow) wirken auf dieses neu geöffnete Fenster (`_STATE["working_doc"]` wird entsprechend nachgezogen).

## `dms_client.py`: reine Standardbibliothek

LibreOffices gebündelter Python-Interpreter hat keine vorinstallierten Drittanbieter-Pakete (kein `requests` ohne zusätzlichen Eingriff) — `urllib.request` ist bewusst die einzige HTTP-Abhängigkeit, kein Installationsschritt für Endnutzer. Spiegelt exakt dieselben Gateway-Aufrufe wie `apps/office-addin/src/lib/api.ts` (P14-S8).

## Vorlagenbibliothek

Identisch zu `apps/office-addin` (P14-S8): eine Vorlage ist ein gewöhnliches Dokument im Wurzelordner `Vorlagen` (Konstante `TEMPLATE_LIBRARY_FOLDER_NAME` in `ogdoc_addin.py`) — Rollenbasiertheit folgt automatisch aus der bestehenden Ordner-Leserechtsprüfung, keine zweite Implementierung dieses Konzepts.

## Build & Installation

```bash
cd apps/libreoffice-addin
python3 build.py                 # erzeugt OgDocAddin.oxt
unopkg add OgDocAddin.oxt         # pro Nutzer installieren (kein --shared ohne Root-Rechte)
```

Menü "Extras > OG Doc öffnen..." erscheint danach in jedem geöffneten Writer-Textdokument.

## Tests

- `python3 -m unittest discover -s apps/libreoffice-addin/tests`: **30 Tests**, reines `unittest`, keine Drittanbieter-Testabhängigkeit.
  - `test_dms_client.py` (6): Multipart-Aufbau (Felder + Datei-Teil, `None`-Werte werden übersprungen), `ApiError`-Übersetzung von HTTP-Fehlern inkl. `detail`-Feld, erfolgreiche JSON-Antworten, Content-Type-Rückgabe beim Herunterladen.
  - `test_settings_store.py` (9): Sitzungs-Persistenz (Laden/Speichern/Löschen, isolierte temporäre Datei je Test) UND **echte** Dokument-Verknüpfungs-Logik gegen einen handgeschriebenen Fake von UNOs `UserDefinedProperties`/`PropertySetInfo` (`tests/uno_mock.py`) — Setzen/Lesen/Überschreiben/Löschen.
  - `test_ogdoc_addin_pure.py` (15): reine Geschäftslogik ohne UNO-Bezug (Hub-Statustext je Anmelde-/Verknüpfungszustand, welche Hub-Buttons je Zustand erscheinen, Attribut-Feld-Filterung, Dateiendungs-Erkennung je Content-Type) — importiert dabei das VOLLSTÄNDIGE `ogdoc_addin.py`-Modul (mit `unohelper`/`com.sun.star.awt` gemockt), ein echter Verdrahtungs-/Referenzfehler-Test.
- **Echte `.oxt`-Installation via `unopkg add`** (siehe ADR 0046 "Verifikation") — Exit 0, `unopkg list --verbose` bestätigt Registrierung von Paket UND `Addons.xcu`, alle vier `python/*.py`-Dateien landen korrekt im von `ScriptProviderForPython` erwarteten Verzeichnis. Danach sauber deinstalliert.
- **Kein funktionierender headless UNO-Skript-Bridge-Zugang** in dieser Entwicklungsumgebung (`soffice --accept=...` scheitert unabhängig vom Transport, siehe ADR 0046 "Verifikation" für die Diagnose) — die Dialog-Bau-/Aktions-Funktionen selbst sind deshalb nur import-/verdrahtungsgeprüft, nicht verhaltensgetestet gegen echte `UnoControlDialog`-Objekte.

## Offene Punkte

- **Kein echter Klick-/Sideload-Test in Writer möglich** in dieser Entwicklungsumgebung (siehe oben) — ein Mensch sollte `OgDocAddin.oxt` vor Produktivnutzung tatsächlich installieren und durchklicken.
- **Nur Writer** — Calc/Impress sind vollständig unangetastet (keine vergleichbare "gesamtes Dokument laden"-API mit derselben Robustheit).
- **Dialog-UI ist bewusst funktional-schlicht** (programmatische AWT-Steuerelemente statt `.xdl`-Ressourcen, ein Textfeld je Attribut ohne Typ-spezifische Widgets) — identische, dokumentierte Vereinfachung wie `apps/office-addin`s `MetadataForm` (P14-S8).
- **Vorlagenbibliothek erfordert manuelle Admin-Einrichtung** (Ordner "Vorlagen" anlegen, Leserechte vergeben) — kein automatisiertes Bootstrap.
- **Kein Löschen von Dokumenten/Ordnern, kein Aufbewahrungs-/Legal-Hold-Zugriff** aus der Erweiterung — bewusst auf den 3.3a-Funktionsumfang beschränkt.
- **`ReferenceOOoMajorMinor`-Versionsprüfungs-Fallstrick** (siehe ADR 0046) — bei künftigen Minimalversions-Änderungen erneut gegen den internen `4.x`-Kompatibilitätswert prüfen, nicht die reale Produktversion.
- **Kein `--shared`-Systeminstallations-Test möglich** (Root-Rechte in dieser Umgebung nicht verfügbar) — nur der reguläre Pro-Nutzer-Installationsweg (`unopkg add` ohne `--shared`) wurde live verifiziert.
