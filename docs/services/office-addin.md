# office-addin

**Verantwortung:** Microsoft-Office-Add-in (Office.js, nur **Word**) für native OG-Doc-Integration (3.3a): Öffnen/Speichern eines Dokuments direkt aus/in OG Doc, inline Metadatenbearbeitung, Workflow-Start/-Fortsetzung, zentrale rollenbasierte Vorlagenbibliothek — jeweils "ohne die DMS-Oberfläche separat aufrufen zu müssen" (Konzept-Wortlaut). P14-S8; die gleichwertige LibreOffice-/OpenOffice-Erweiterung (UNO-`.oxt`) ist P14-S9, eine separate Session.

**Konzept-Referenz:** 3.3a, 3.3, 7.1, 2.2, 2.5
**Kein eigenes Postgres-Schema** — reine clientseitig gerenderte SPA (statischer Export, gleiches Muster wie `apps/user-ui`, ADR 0006), kein eigener Backend-Prozess. Spricht **keinen einzigen neuen Endpunkt** an — vollständige Wiederverwendung von `document-service`/`workflow-service`/`object-type-service`/`folder-service`/`search-service`/`auth-service`.
**ADR:** [0045 — Nur Word, vollständige Endpunkt-Wiederverwendung, Dokument-Verknüpfung über `document.settings`](../adr/0045-office-addin-word-only-reused-endpoints-settings-linking.md)

## Ort im Repo

`apps/office-addin/` — Next.js-Static-Export + `manifest.xml` (Office-Add-in-Manifest, XML-Format, breiter kompatibel als das neuere JSON-Manifest) + `assets/` (Icons). `manifest.xml`/`assets/` sind **kein** Next.js-Build-Ausgang, werden separat ins Docker-Image kopiert (siehe `Dockerfile`).

## Funktionsumfang

| Bereich | Umsetzung |
|---|---|
| **Aus OG Doc öffnen** | `DocumentPicker` (Volltextsuche über `search-service`, 3.7/3.7a) → Inhalt laden (`GET /documents/{id}/content`) → `Word.run(... insertFileFromBase64(base64, InsertLocation.replace))` ersetzt den gesamten Word-Dokumentinhalt → Sperre versuchen (`POST /documents/{id}/lock`) → Verknüpfung in `Office.context.document.settings` persistieren. |
| **Neu aus Vorlage** | `TemplatePicker` listet Dokumente aus dem Wurzelordner "Vorlagen" (Name konfigurierbar, siehe unten) → Inhalt laden + ins aktuell leere Word-Dokument laden (identischer `insertFileFromBase64`-Weg) → beim ersten Speichern `POST /documents` mit `derived_from_document_id`/`derived_from_version_number` = die Vorlage. |
| **In OG Doc speichern** | `Office.context.document.getFileAsync(Compressed, ...)` liest die aktuellen Word-Rohbytes → `POST /documents/{id}/versions` (Check-in, `expected_base_version_number` = zuletzt bekannte Version — optimistische Konflikterkennung wie jeder andere Check-in-Client, bei Versionsabweichung entsteht eine Konfliktkopie statt eines Fehlers). |
| **Inline-Metadaten** | `MetadataForm` (Titel + ein Textfeld je Objekttyp-Attribut, `GET /object-types/{id}` für das Schema) → `PATCH /documents/{id}`. |
| **Workflow starten/fortsetzen** | `WorkflowPanel`: `GET /instances?business_key={documentId}` + `GET /instances/{id}/tasks` für laufende Instanzen dieses Dokuments, `POST /instances/{id}/tasks/{id}/complete` zum Abschließen, `POST /process-definitions/{id}/instances` mit `business_key={documentId}` zum Start eines neuen Workflows. |

## Dokument-Verknüpfung: `Office.context.document.settings` statt Server-Zustand

Welches OG-Doc-Dokument (ID + zuletzt bekannte Versionsnummer) zur gerade geöffneten Word-Datei gehört, wird über `Office.context.document.settings` gespeichert — add-in-eigener Zustand, der **in der Datei selbst** (eigene XML-Custom-Part) landet. Nach Schließen und erneutem Öffnen derselben Datei (mit aktiviertem Add-in) ist die Verknüpfung automatisch wieder vorhanden, ohne dass ein Backend eine Datei-zu-Dokument-Zuordnung pflegen müsste. Siehe `src/lib/office.ts` (`getLinkedDocument`/`setLinkedDocument`/`clearLinkedDocument`).

## Sperren statt nur optimistischer Konfliktprüfung

Anders als `user-ui` (das bewusst nur die optimistische Versionsprüfung beim Check-in nutzt, ADR 0002) verwendet dieser Add-in die bereits vorhandene, bislang von keinem Frontend genutzte explizite Sperre (`POST`/`DELETE /documents/{id}/lock`) — eine Word-Bearbeitungssitzung kann lange dauern, ein "wird gerade von jemand anderem bearbeitet"-Hinweis VOR Bearbeitungsbeginn ist hier sinnvoller als nur ein Konflikt beim Speichern. Schlägt die Sperre fehl (`409`, jemand anderes hält sie bereits), wird das Dokument trotzdem schreibgeschützt geöffnet (Titel/Metadaten/Workflow bleiben lesbar), aber der "In OG Doc speichern"-Button ist deaktiviert (`document-service`s `checkin_version` würde den Schreibversuch ohnehin serverseitig ablehnen).

## Vorlagenbibliothek (3.3a) — Namenskonvention statt neuem Mechanismus

Eine Vorlage ist ein **gewöhnliches Dokument** im Wurzelordner `Vorlagen` (Name über `NEXT_PUBLIC_TEMPLATE_LIBRARY_FOLDER_NAME`/`OFFICE_ADDIN_TEMPLATE_LIBRARY_FOLDER_NAME` konfigurierbar) — "rollenbasiert" (Konzept-Wortlaut) ist damit automatisch die bereits bestehende Ordner-Leserechtsprüfung (`permission-service`), keine neue Berechtigungslogik, kein neuer Endpunkt. Ein Admin legt den Ordner manuell an und vergibt Leserechte wie für jeden anderen Ordner. **Nicht** dasselbe Konzept wie die künftige strukturelle "Vorlagen" (2.5/P15-S6, Aktenplan-Rohbau über den JSON-Struktur-Export) — siehe ADR 0045 für die Abgrenzung.

## Anbindung an das Backend

Ausschließlich über das API-Gateway (3.5), keine direkten Backend-Adressen:

| Aktion | Gateway-Aufruf |
|---|---|
| Anmelden / Identität | `POST /api/auth-service/login`, `GET /api/auth-service/me` |
| Suchen (Dokument-Picker) | `GET /api/search-service/search?q=` |
| Dokument lesen/Inhalt | `GET /api/document-service/documents/{id}`, `GET /api/document-service/documents/{id}/content` |
| Neue Version einchecken | `POST /api/document-service/documents/{id}/versions` |
| Neues Dokument anlegen (aus Vorlage) | `POST /api/document-service/documents` |
| Metadaten ändern | `PATCH /api/document-service/documents/{id}` |
| Sperren/Entsperren | `POST`/`DELETE /api/document-service/documents/{id}/lock` |
| Wurzelordner/Vorlagenliste | `GET /api/folder-service/folders/root/children`, `GET /api/document-service/documents?folder_id=` |
| Objekttyp-Schema | `GET /api/object-type-service/object-types/{id}` |
| Workflow | `GET /api/workflow-service/process-definitions`, `GET /api/workflow-service/instances?business_key=`, `GET /api/workflow-service/instances/{id}/tasks`, `POST /api/workflow-service/instances/{id}/tasks/{id}/complete`, `POST /api/workflow-service/process-definitions/{id}/instances` |

## Auth

Identisches Muster wie `reviewer-ui`/`migration-console`: einfaches Login-Formular (`POST /login`), Tokens im `localStorage` (`ogdoc.tokens`, ADR 0006). Besondere Nuance im Office-Taskpane-Kontext: der Speicherort/die Lebensdauer des Taskpane-Webviews unterscheidet sich je Office-Version/-Plattform — ein erneutes Anmelden nach einem Word-Neustart ist ein erwartbarer, kein fehlerhafter Fall (siehe "Offene Punkte").

## Manifest & Deployment

`manifest.xml` (XML-Format, `TaskPaneApp`, Host `Document` = Word) deklariert einen einzigen Ribbon-Button auf dem Home-Tab, der ausschließlich den Taskpane öffnet (`ShowTaskpane` — keine separate `FunctionFile`-Logik nötig, jede Interaktion läuft innerhalb des Taskpanes). Verifiziert mit dem offiziellen `office-addin-manifest validate`-Tool (Microsoft) — "The manifest is valid.", lauffähig auf Word 2013+/Windows/Mac/Web laut Manifest-Struktur.

**HTTPS-Pflicht**: Office lädt Add-in-Webinhalte nur über HTTPS (von wenigen dokumentierten lokalen Ausnahmen abgesehen). Dieser Stack läuft in der Entwicklungsumgebung durchgehend über HTTP wie jeder andere Dienst — `office-addin` bräuchte für einen echten Sideload-Test einen eigenen TLS-Terminierungspunkt (siehe README.md "Lokales Sideload-Testen").

## Tests

- `npm run typecheck`/`npm run lint`/`npm run build` — sauber.
- `npm test` (Vitest): **18 Tests**.
  - `tests/office-lib.test.ts` (8): `lib/office.ts` gegen einen handgeschriebenen `Office`/`Word`-Fake (`tests/office-mock.ts`) — Verknüpfung setzen/lesen/löschen inkl. `saveAsync`, `insertFileFromBase64` erhält den erwarteten Base64-Inhalt, Datei-Slices werden korrekt zu einer Base64-Zeichenkette zusammengesetzt, `base64ToBlob`/`blobToBase64`-Rundreise.
  - `tests/auth-context.test.tsx` (4): identisches Login/Logout/Sitzungs-Wiederherstellungs-Muster wie die übrigen Apps, eigener Storage-Key (`ogdoc.tokens`).
  - `tests/task-pane.test.tsx` (6): Leerzustand zeigt Dokument-/Vorlagen-Picker; Öffnen eines Dokuments lädt es via `Word.run` ins Dokument und verknüpft es; ein Sperrkonflikt (`409`) zeigt einen Nur-Lesen-Hinweis und deaktiviert Speichern; "Neu aus Vorlage" legt beim ersten Speichern ein neues Dokument mit korrektem `derivedFromDocumentId`/`derivedFromVersionNumber` an; Speichern schickt die erwartete `expected_base_version_number` und aktualisiert die verknüpfte Version; Verknüpfung lösen gibt die Sperre frei und kehrt zur Picker-Ansicht zurück.
- **`npx office-addin-manifest validate manifest.xml`** (echtes, offizielles Microsoft-Tool) — "The manifest is valid.", keine Warnungen.
- **Live gegen den echten laufenden Stack** (curl, kein echter Office-Host verfügbar): alle wiederverwendeten Backend-Endpunkte einzeln nachvollzogen — siehe "Offene Punkte" für die dabei bewusst nicht mögliche Verifikation.

## Offene Punkte

- **Keine Verifikation gegen einen echten Office-Host möglich** — kein Windows/Office/gültiger Microsoft-365-Sideloading-Mandant in dieser Entwicklungsumgebung, keine headless/containerisierte Möglichkeit, Word tatsächlich auszuführen (anders als der ephemere-Playwright-Ansatz für Browser-UIs). Ein Mensch sollte das Add-in vor Produktivnutzung tatsächlich in Word sideloaden und durchklicken.
- **Nur Word** — Excel/PowerPoint/Outlook sind vollständig unangetastet (keine vergleichbare "gesamtes Dokument ersetzen"-JS-API verfügbar, siehe ADR 0045).
- **Kein Theme-Umschalter/Wartungsbanner** — bewusst weggelassen (Platzgründe, ein Add-in sollte sich idealerweise am Office-eigenen Theme orientieren statt einem eigenen Schalter).
- **`MetadataForm` ist ein einfaches Ein-Textfeld-je-Attribut-Formular** — keine typspezifischen Widgets/Layout-Anordnung wie `user-ui`s `LayoutFormFields` (2.2b), angemessen für die schmale Taskpane-Breite.
- **Vorlagenbibliothek erfordert manuelle Admin-Einrichtung** (Ordner "Vorlagen" anlegen, Leserechte vergeben) — kein automatisiertes Bootstrap, kein Admin-UI-Baustein dafür.
- **Kein Löschen von Dokumenten/Ordnern, kein Aufbewahrungs-/Legal-Hold-Zugriff** aus dem Taskpane — bewusst auf den 3.3a-Funktionsumfang beschränkt.
- **HTTPS-Terminierung für einen echten Sideload-Test nicht Teil dieser Session** (siehe oben).
- **Tokens im `localStorage`** (ADR 0006) — im Taskpane-Webview-Kontext mit einer zusätzlichen, plattformabhängigen Nuance (Webview-Lebensdauer variiert je Office-Version), siehe "Auth" oben.
