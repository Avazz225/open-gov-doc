# admin-ui

**Verantwortung:** Administrative Web-Oberfläche — Nutzer-/Rollenverwaltung, Objekttyp-Editor, Registry-/Service-Übersicht, Verwaltung mehrerer Installationen aus einer Admin-UI heraus (Konzept 8).
**Konzept-Referenz:** 8, 3a, 3.10
**Kein eigenes Postgres-Schema** — reine clientseitig gerenderte SPA (statischer Export, siehe [ADR 0006](../adr/0006-user-ui-static-export-spa.md)), kein eigener Backend-Prozess.

## Ort im Repo

`apps/admin-ui/` — identisches Muster wie `apps/user-ui` (nicht unter `services/`, siehe ADR 0006): Next.js/TypeScript, `output: "export"`, Auslieferung über `nginx`, kein Node-Prozess zur Laufzeit.

## Seiten

| Route | Zweck |
|---|---|
| `/login/` | Anmeldung (identischer Ablauf wie User-UI, gegen den Auth Service der **aktiven Installation** über deren Gateway) |
| `/` | Startseite mit Hinweistext, Navigation läuft über die Seitenleiste (`AdminShell`) |
| `/users/` | Nutzer anlegen/löschen, Rollen anlegen, Rollenzuweisungen anlegen/entfernen — seit **Post-Roadmap Phase 22 Session 2** zusätzlich Gruppen anlegen/löschen + Mitgliederverwaltung, siehe unten |
| `/object-types/` | Objekttypen anlegen/bearbeiten/löschen über einen geführten Formular-Assistenten (`ObjectTypeEditor`, seit P5b-S3, seit P5e-S3 inkl. Kennzeichengenerator-Format/Anzeige-Override, seit **P6-S7** inkl. Mindest-Signaturniveau, 3.10, seit **P7-S3** inkl. Aussonderungs-Frist/Verschlüsselung, 5.6) + Formular-Layout-Designer (`LayoutDesigner`, 2.2b) |
| `/registry/` | Alle bei der Registry registrierten Instanzen inkl. Health-Status |
| `/teamspaces/` | Teamspaces-Admin-Übersicht (`TeamspacesAdmin`, 2.5, seit **Post-Roadmap Phase 22 Session 5**, [ADR 0090](../adr/0090-teamspaces-admin-overview.md)) — installationsweite Statustabelle aller Teamspaces gegen `teamspace-service`s neuen `GET /admin/teamspaces`, hinter der Capability `admin.teamspace_management` (`RequireCapability` UND gegateter Sidebar-Eintrag, echte serverseitige Durchsetzung), siehe unten |
| `/installations/` | Installationsliste verwalten (anlegen/löschen/wechseln) — seit P4-S5 |
| `/ocr-settings/` | Maximale Wortobergrenze/Verarbeitungs-Batch-Size/Content-Type-Positivliste des OCR Service (`OcrSettings`, seit P5b-S5, Positivliste seit P5d-S1) |
| `/signature-config/` | Signaturniveaus je Connector des Signature Service (`SignatureConfig`, 3.10, seit **Post-Roadmap Phase 22 Session 6**, [ADR 0091](../adr/0091-connector-operational-config-live-editable.md)) — **erste Admin-UI-Anbindung von `signature-service` überhaupt** |
| `/upload-settings/` | Format-Whitelist des Document Service (`UploadSettings`, seit P5d-S1) |
| `/storage-guard/` | Datenträger-Wechsel-Wächter-Status + Admin-Override des Storage Service (`StorageGuard`, seit P5b-S6) |
| `/storage-operational-config/` | Schreibstrategie/Quorum/Max-Replikationsversuche des Storage Service (`StorageOperationalConfig`, 3.6, seit **Post-Roadmap Phase 22 Session 6**, [ADR 0091](../adr/0091-connector-operational-config-live-editable.md)) |
| `/kennzeichen-settings/` | Globaler Standard "Kennzeichen vor Dateinamen anzeigen" des Object-Type Service (`KennzeichenSettings`, seit P5e-S3) |
| `/retention-settings/` | Installationsweite Aufbewahrungs-/Papierkorb-Konfiguration (`RetentionSettings`, 5.2/5.2a, seit P7-S1) — zwei unabhängige Sektionen seit P7-S1b: Document Service und Folder Service (eigene, unabhängig konfigurierbare Configs) |
| `/deletion-register/` | Löschregister lesen (`DeletionRegister`, reine Lese-Tabelle, 5.2a, seit P7-S1) — zeigt seit P7-S1b Dokumente **und** Ordner gemeinsam in einer Tabelle (Spalte "Typ") |
| `/archival-transfers/` | Aussonderungs-Transfer-Status + Rückholung (`ArchivalTransfersView`, 5.6, seit P7-S3, seit **P7-S3b** zusätzlich ein zweiter Abschnitt für Umlaufmappen, seit **Post-Roadmap Phase 20 Session 7** zusätzlich `failed_permanent`-Filter + Neustart-Button in beiden Abschnitten) — reine Statustabelle(n) gegen `archival-service`/`case-service`, siehe unten |
| `/processing-failures/` | "Permanent fehlgeschlagen"-Sichtbarkeit + manueller Neustart (`ProcessingFailuresView`, seit **Post-Roadmap Phase 20 Session 7**, [ADR 0083](../adr/0083-admin-ui-processing-failures-visibility.md)) — drei Sektionen (Benachrichtigungen/Ersatzdarstellungen/OCR-Ergebnisse) gegen `notification-service`/`rendering-service`/`ocr-service`, siehe unten |
| `/superuser/` | Superuser-Break-Glass-Status + Aktivierung anfordern/genehmigen (`SuperuserBreakGlass`, 4.6, seit P6-S5) — dünner Aufrufer der bestehenden generischen Approval-Endpunkte des Permission Service (P6-S4). Seit **P6-S6** zusätzlich ein Abschnitt "Systemweite Notfallsperre" (Not-Shutdown, 4.8): Status, Auslösen-Formular (nur sichtbar mit der Capability `system.not_shutdown.trigger`), Aufheben-Button (nur sichtbar für den aktuell angemeldeten, aktiven Superuser) |
| `/reports/` | Standardberichte (`ReportsView`, 5.4a, seit P7-S2b) — vier Berichtsabschnitte + Verwaltung planbarer E-Mail-Läufe gegen den neuen `reporting-service`, siehe unten |
| `/forensic-trace/` | Forensik-Trace (`ForensicTraceView`, 5.4b, seit P7-S2c) — objektbezogene Nachverfolgung (Nutzer/Dokument/Ordner), Kategorie-/Zeitfensterfilter, Anomalie-Banner, CSV-/PDF-Export, siehe unten |
| `/audit-trace-settings/` | Audit-Tiefe für den Forensik-Trace (`AuditTraceSettings`, 5.4b, seit P7-S2c) — Basis-Konfiguration + Rollen-Overrides gegen den Document Service, siehe unten |
| `/query-console/` | Query- & Trace-Konsole (`QueryConsoleView`, 6.1, seit P8-S1, seit **P8-S2b** zusätzlich der Manipulations-Abschnitt) — strukturierte, RBAC-gefilterte Lesezugriffe + Schutzschalter/Dry-Run/Vier-Augen-Manipulation gegen `query-service`, siehe unten |
| `/share-link-settings/` | Installationsweiter Schalter + maximale Gültigkeitsdauer für den öffentlichen Freigabelink (`ShareLinkSettings`, 4.2a, seit P14-S10) — gleiches Lade-/Speicher-Muster wie `OcrSettings`, gegen den Document Service |
| `/delegations/` | Installationsweite Übersicht über alle Stellvertretungen (`DelegationsAdmin`, 4.4a, seit P14-S11) — Anlegen bleibt Selbstverwaltung (User-UI), hier nur Überblick + Admin-Widerruf |
| `/config-packages/` | Konfigurationspakete laden/vorschauen/anwenden (`ConfigPackages`, 14.1, seit P17-S1) — **erste Admin-UI-Anbindung von `config-service` überhaupt**, siehe unten |
| `/approval-settings/` | Generische Vier-Augen-Einstellungen (`ApprovalSettings`, 4.3, seit **Post-Roadmap Phase 22 Session 3**, [ADR 0089](../adr/0089-approval-settings-ui-config-endpoint-stays-ungated.md)) — Toggle je konfiguriertem Aktionstyp + Formular zum erstmaligen Konfigurieren eines neuen, siehe unten |

Alle Seiten außer `/login/` sind über `RequireAuth` geschützt (clientseitiger Redirect, kein Server für Middleware verfügbar — wie bei der User-UI). `RequireAuth` prüft die Sitzung der **aktiven Installation**. `/users/` ist seit **P6-S5** zusätzlich über `RequireCapability` geschützt (Domäne "Nutzer-/Rechteverwaltung", 4.6) — siehe "Autorisierung" unten.

## Gruppen-Verwaltung (Post-Roadmap Phase 22 Session 2)

Neue Sektion "Gruppen" in `UserManagement.tsx` (`/users/`), zwischen den bestehenden Rollen- und
Rollenzuweisungs-Sektionen. Nutzt `permission-service`s neue, admin-anlegbare Gruppen (siehe
`docs/services/permission-service.md` "Admin-anlegbare Gruppen"), die die seit Phase 19 Session 2
bestehende, hartkodierte "everyone"-Gruppe um echte Mitgliederlisten ergänzen.

- Formular zum Anlegen (Name + Beschreibung), Tabelle aller Gruppen mit "Mitglieder anzeigen"/"Löschen"
  je Zeile. Aufklappen einer Zeile (gleiches Auf-/Zuklapp-Muster wie `ProcessDefinitionList`s
  Versionshistorie) lädt lazy die Mitgliederliste (`GET /groups/{id}/members`) nach, zeigt ein
  Mini-Formular zum Hinzufügen (Nutzername/Principal-ID) sowie einen "Entfernen"-Button je Mitglied.
- Ein Nutzer wird zu einer Gruppe über seine `principal_id` (i. d. R. der Nutzername) hinzugefügt — keine
  Auswahl aus der bestehenden Nutzerliste, freies Textfeld (gleiche bewusste Einfachheit wie bei
  `roleAssignments.username`).
- **Kein neuer RBAC-Sonderfall im Frontend**: die Sichtbarkeit der gesamten `/users/`-Seite ist bereits
  seit P6-S5 über `RequireCapability` gegated (s. o.); das serverseitige `403` von `permission-service`
  bei fehlender `admin.user_management`-Capability ist die eigentliche Durchsetzung, identisch zum
  bestehenden Muster bei Rollen-Anlage.

## Vier-Augen-Einstellungen (Post-Roadmap Phase 22 Session 3, [ADR 0089](../adr/0089-approval-settings-ui-config-endpoint-stays-ungated.md))

Neue Seite `/approval-settings/` (`ApprovalSettings.tsx`) — erste Admin-UI-Anbindung des generischen
Vier-Augen-Mechanismus (4.3) selbst, bislang nur per `curl`/direktem HTTP-Aufruf konfigurierbar.

- **Tabelle bereits konfigurierter Aktionstypen** (`GET /approval-config`, alphabetisch sortiert): jede
  Zeile zeigt `action_type`, eine Checkbox für `requires_approval` (klicken schaltet sofort per `PUT
  /approval-config/{action_type}` um und lädt neu), `required_permission` (falls gesetzt, sonst "—") und
  den letzten Änderungszeitpunkt. **`GET /approval-config` liefert nur Zeilen mit bereits mindestens
  einem `PUT`-Aufruf** — kein fester Katalog aller im System existierenden Aktionstypen (siehe
  `docs/services/permission-service.md`), daher ist ein leerer Zustand ("Noch keine Aktionstypen
  konfiguriert") ein normaler Startzustand, kein Fehler.
- **Formular "Neuen Aktionstyp konfigurieren"**: Freitext-`action_type` (z. B. `document.force_unlock`)
  + `requires_approval`-Checkbox + optionales `required_permission`-Feld, ruft denselben `PUT`-Endpunkt
  auf. Einziger Weg in der Admin-UI, einen bislang unkonfigurierten Aktionstyp erstmals zu setzen.
- **Wichtige Korrektheitsregel beim Umschalten**: `required_permission` wird beim Umschalten-Klick
  IMMER mit dem zuletzt geladenen Wert der Zeile mitgeschickt (nie weggelassen) — `PUT
  /approval-config/{action_type}` überschreibt das Feld sonst mit `null`, was z. B. `auth.superuser.
  activate`s Break-Glass-Rollenbindung (`breakglass.approve`) stillschweigend löschen würde.
- **Bewusst kein `RequireCapability`-Wrapper und keine `requiresCapability` am Sidebar-Eintrag** — anders
  als `/users/`: `PUT /approval-config/{action_type}` blieb in dieser Session bewusst ungegatet (siehe
  ADR 0089 für die vollständige Begründung, insbesondere die Blast-Radius-Analyse über acht betroffene
  Testsuiten), ein clientseitiges Capability-Gate würde eine serverseitig nicht existierende Durchsetzung
  vortäuschen — gleiche Disziplin wie bei `ArchivalTransfersView`s ungegatetem Rückholen-Button.

## Teamspaces-Admin-Übersicht (Post-Roadmap Phase 22 Session 5, [ADR 0090](../adr/0090-teamspaces-admin-overview.md))

Erste Admin-UI-Anbindung von `teamspace-service` überhaupt. `TeamspacesAdmin.tsx` (`/teamspaces/`) —
reine Statustabelle (Name, Beschreibung, Angelegt von, Mitgliederzahl, Angelegt am) gegen den neuen
`GET /api/teamspace-service/admin/teamspaces` — installationsweit, unabhängig von der eigenen
Mitgliedschaft (anders als `GET /teamspaces`, das nach Mitgliedschaft filtert und in dieser App nirgends
verwendet wird). Bewusst **keine** administrativen Aktionen (Löschen/Mitgliederverwaltung) — Teamspaces
bleiben Selbstverwaltung (2.5), diese Seite ist reine Sichtbarkeit für eine Aufsichtsrolle. Anders als
`/approval-settings/` (P22-S3, dort bewusst ungegatet) hat dieser Endpunkt eine echte serverseitige
Durchsetzung (`admin.teamspace_management`) — die Seite ist deshalb konsequent sowohl per
`RequireCapability` als auch per gegatetem Sidebar-Eintrag geschützt, wie `/users/`.

## Layout (P4-S5, Nutzer-Feedback nach dem ersten echten Browser-Test des MVP)

Ersetzt die frühere flache Top-Nav-Leiste durch ein klassisches Management-Dashboard-Layout (Konzept 8):

- **`AdminSidebar`** (links): gruppierte, einzeln ausklapp-/einklappbare Navigation (`sidebar-group`-Blöcke, Auf-/Zuklapp-Zustand pro Browser in `localStorage` gemerkt). Gruppen — "Verwaltung" (Nutzer & Rollen, Objekttypen, seit P5e-S3 zusätzlich Kennzeichen-Einstellungen, seit **P17-S1** zusätzlich Konfigurationspakete — nur mit der Capability `admin.object_config` sichtbar, Registry), "Installationen", seit P5b-S5 "Verarbeitung" (OCR-Einstellungen, seit P5d-S1 zusätzlich Format-Whitelist), seit P5b-S6 "Speicher" (Speicher-Wächter), seit **P7-S1** "Compliance" (Aufbewahrungs-Einstellungen, Löschregister, seit **P7-S3** zusätzlich Aussonderung & Archivierung), seit **P6-S5** "Sicherheit" (Superuser Break-Glass, seit **P7-S2c** zusätzlich Forensik-Trace und Audit-Tiefe), seit **P7-S2b** "Berichte" (Standardberichte) und seit **P8-S1** "Diagnose-Werkzeuge" (Query-Konsole, nur mit der Capability `admin.query_console` sichtbar) — generisch gebaut für weitere Gruppen in künftigen Sessions. Der Manipulations-Abschnitt der Query-Konsole (seit **P8-S2b**) ist zusätzlich innerhalb der Seite selbst hinter der feingranularen Capability `admin.query_console.manipulate` versteckt (nicht nur der Sidebar-Eintrag).
- **`AdminShell`**: Kopfzeile (Titel, `InstallationSwitcher`, `ThemeSwitcher` seit P4-S6, Nutzername, Abmelden) + Hauptbereich rechts, der die jeweils gewählte Seite zeigt. Seit **P6-S6** rendert `AdminShell` zusätzlich als erstes Kind ein globales `MaintenanceBanner` (Not-Shutdown, 4.8) — pollt `GET /api/permission-service/maintenance-mode` alle 30s, zeigt bei aktivem Wartungsmodus einen auffälligen Hinweistext über der gesamten Seite, sonst `null`; bleibt bei unerreichbarem Permission Service bewusst still (fail-open, keine Fehlermeldung).

## Geführter Objekttyp-Editor + Formular-Layout-Designer (2.2/2.2a/2.2b, seit P5b-S3)

Ersetzt den JSON-Freitext-Attribut-Editor aus P4-S3 durch zwei getrennte Bereiche auf derselben Seite:

- **`ObjectTypeEditor`**: seit **P6-S7** zusätzlich ein bedingtes Dropdown "Mindest-Signaturniveau" (keins/SES/AES/QES, nur bei `applies_to="document"` sichtbar, `required_signature_level`, 3.10) — reine Konfiguration, die tatsächliche Durchsetzung passiert beim Signiervorgang im Signature Service, siehe `docs/services/signature-service.md`. Strukturierter Attribut-Builder statt JSON-Textarea (Zeile je Attribut: technischer Name, Typ, Pflichtfeld, je nach Typ zusätzlich Muster/Minimum/Maximum), Checkboxen für `allowedParentTypes` (2.2a: `"$ROOT"` + alle bestehenden Ordnerklassen außer der gerade bearbeiteten selbst) und ein `icon`-Auswahlfeld (2.2a/2.2b, nur bei `applies_to="folder"` sichtbar) aus einem **kuratierten, fest verdrahteten Icon-Set** (`folder`, `folder-open`, `folder-star`, `archive`, `briefcase`, `invoice`, `contract` — bewusste Entscheidung gegen freien SVG-Upload, siehe Konzept 13 "offene Punkte": ein kuratiertes Set vermeidet die dort benannte Sicherheitsfrage aktiver Inhalte in hochgeladenen SVGs vollständig, auf Kosten von Gestaltungsfreiheit). Unterstützt jetzt auch das **Bearbeiten** bestehender Objekttypen (`PUT /object-types/{id}`) — `name`/`applies_to` bleiben dabei serverseitig unveränderlich und sind im Formular gesperrt; `naming_constraints`/`conditions` werden beim Speichern unverändert durchgereicht (keine UI dafür in dieser Session), damit ein Speichern über den neuen Editor sie nicht stillschweigend auf ihren Default zurücksetzt.
- **Anzeigenamen sind eine Anlage-Zeit-Funktion, keine dauerhaft editierbare Attribut-Eigenschaft**: Labels leben im Formular-Layout, nicht im Attribut-Schema selbst (siehe [ADR 0014](../adr/0014-form-layout-generated-defaults-not-persisted.md)). Nur beim **Neuanlegen** eines Objekttyps zeigt der Attribut-Builder ein zusätzliches "Anzeigename"-Feld je Attribut; weicht mindestens ein Anzeigename vom technischen Namen ab, wird direkt nach dem Anlegen ein initiales Smart Layout (2 Attribute pro Zeile, wie `object_type_service.layout.generate_smart_layout`) mit diesen Labels für alle drei Verwendungszwecke (Anzeige/Suche/Upload) gespeichert (`PUT .../layouts/{purpose}` dreimal). Ohne abweichende Labels bleibt es beim serverseitig generierten Default, kein unnötiges Override. Beim **Bearbeiten** eines bestehenden Objekttyps fehlt das Anzeigename-Feld bewusst — spätere Label-Anpassungen laufen ausschließlich über den Layout-Designer, damit der Objekttyp-Editor keine dort bereits vorgenommenen, gezielten Layout-Anpassungen überschreibt.
- **`LayoutDesigner`** (eigener Bereich unterhalb des Objekttyp-Editors): Objekttyp- und Verwendungszweck-Auswahl (Anzeige/Suche/Upload), zeigt das aktuelle Layout inkl. Badge ("Automatisch generiert" vs. "Angepasst", aus `is_custom` der API). Bearbeitung ausschließlich über eindeutige Zeilenoperationen statt freiem Ziehen einzelner Felder zwischen Zeilen (Zeile nach oben/unten verschieben, Zeile entfernen, neue leere Zeile anlegen, Feld innerhalb einer Zeile nach links/rechts verschieben, Feld aus der Zeile entfernen, Attribut aus den noch nicht verwendeten Attributen gezielt in eine bestimmte Zeile hinzufügen) — ein Feld zwischen zwei Zeilen zu verschieben braucht deshalb zwei Schritte (entfernen, dann in der Zielzeile hinzufügen) statt einer einzelnen Geste; bewusst so entschieden, da diese Umgebung keinen Browser für eine visuelle Verifikation einer Drag-&-Drop-Interaktion hat (siehe "Tests" unten) und jede Zeilenoperation dafür eindeutig und mit Vitest testbar bleibt. "Speichern" (`PUT`) und "Auf generiertes Layout zurücksetzen" (`DELETE`, danach Neuladen) je Objekttyp und Verwendungszweck unabhängig voneinander.
- **Kennzeichengenerator-Felder** (2.2, seit P5e-S3): nur bei `applies_to="document"` sichtbar — ein Format-String-Freitextfeld (`kennzeichen_format`, Platzhalter-Hinweis als `hint`-Text) und ein Tri-State-Auswahlfeld für `kennzeichen_display_override` ("Globalen Standard verwenden"/"Immer anzeigen"/"Nie anzeigen"). Client-seitig auf `null` erzwungen, sobald `applies_to="folder"` gewählt wird — spiegelt dieselbe Zero-Trust-Haltung wie beim `icon`-Feld (serverseitige 422-Validierung bleibt die eigentliche Absicherung, das Frontend vermeidet nur den unnötigen Fehlerfall).
- **Aufbewahrungs-Felder** (5.2/5.2a, seit P7-S1): anders als die Kennzeichen-/Signatur-Felder **für beide `applies_to`-Werte sichtbar** (Objekttyp-Schema gilt gleichermaßen für Dokumente und — ab P7-S1b — Ordner) — ein Zahlenfeld "Standard-Aufbewahrungsfrist in Tagen" (`default_retention_days`, leer = kein Default) und ein Tri-State-Auswahlfeld für `deletion_reason_required_override` ("Globalen Standard verwenden"/"Immer erforderlich"/"Nie erforderlich").
- **Verschlusssachen-Einstufung** (2.5, seit P15-S1, mehrstufig seit P17-S2/14.2): nur bei `applies_to="document"` sichtbar — ein `<select>` mit den vier gängigen deutschen VS-Einstufungen (`VS-NfD`/`VS-VERTRAULICH`/`GEHEIM`/`STRENG GEHEIM`) plus "Nicht eingestuft". **Bis P17-S1 eine reine Checkbox** (`is_classified: bool`) — ersetzt, nicht ergänzt, da das Backend-Feld selbst zu `classification_level: str | null` wurde, siehe [ADR 0059](../adr/0059-egov-paket-aktenplan-hierarchie-und-mehrstufige-vs-einstufung.md).

## Kennzeichen-Einstellungen (2.2/8, seit P5e-S3)

`KennzeichenSettings` (`/kennzeichen-settings/`) editiert denselben Konfigurations-Typ wie `OcrSettings`/`UploadSettings`/`StorageGuard`, diesmal aber gegen den **Object-Type Service** (`GET`/`PUT /api/object-type-service/kennzeichen-config`): ein einziger Checkbox-Schalter "Kennzeichen vor Dateinamen anzeigen" als globaler Standard — bewusst kein Katalog mehrerer unabhängiger Anzeigepunkte (Tab-Titel, Listen-Präfix einzeln togglebar), siehe `PROGRESS.md` "Kennzeichengenerator" für die Begründung dieser Vereinfachung. Einzelne Dokumentenarten überschreiben diesen Standard über das Tri-State-Feld im Objekttyp-Editor. Gleiches "nicht erreichbar"-Leerzustand-Muster wie die übrigen Konfigurationsseiten bei einem Verbindungsfehler.

## OCR-Einstellungen (3.9, seit P5b-S5, [ADR 0016](../adr/0016-ocr-configurability-compose-profile-and-live-settings.md))

`OcrSettings` (`/ocr-settings/`) ist die erste Admin-UI-Seite dieses Projekts, die eine **Backend-Laufzeit-Konfiguration** editiert statt eines Fachobjekts (Nutzer, Objekttyp, ...) — lädt `GET /api/ocr-service/config` und speichert Änderungen an maximaler Wortobergrenze (leer = keine Obergrenze), Verarbeitungs-Batch-Size und (seit P5d-S1) einer kommagetrennten Content-Type-Positivliste über `PUT` (leer = keine Einschränkung, nicht leer = nur gelistete Content-Types lösen OCR aus). Absichtlich **kein** `ocrEnabled`-Schalter auf dieser Seite: Ob OCR überhaupt läuft, ist ein Docker-Compose-Profil-Opt-out (der Container wird gar nicht deployt) — eine Admin-UI kann einen nicht laufenden Service nicht per Knopfdruck starten. Schlägt der Ladeversuch mit einem Verbindungsfehler fehl (kein HTTP-Statuscode, `ApiError` wird nicht geworfen), zeigt die Seite stattdessen einen erklärenden Leerzustand ("nicht erreichbar, vermutlich per Compose-Profil deaktiviert") — das ist die einzige Sichtbarkeit von `ocrEnabled=false` in dieser UI.

## Format-Whitelist (3.1/3.6, seit P5d-S1)

`UploadSettings` (`/upload-settings/`) editiert dieselbe Art Backend-Laufzeit-Konfiguration wie `OcrSettings`, diesmal für den Document Service: `GET`/`PUT /api/document-service/upload-config`, eine kommagetrennte Liste erlaubter Content-Types (leer = keine Einschränkung). Der Hinweistext macht explizit, dass der geprüfte Content-Type aus den tatsächlichen Datei-Bytes ermittelt wird, nicht aus dem vom Browser gesendeten Header — die Whitelist greift also auch dann korrekt, wenn ein Client einen falschen/generischen Content-Type mitschickt.

## Speicher-Wächter (3.6, seit P5b-S6, [ADR 0017](../adr/0017-storage-device-identity-guard.md))

`StorageGuard` (`/storage-guard/`) zeigt je konfiguriertem Ziel des Storage Service (`GET /api/storage-service/guard-status`) die zuletzt bestätigte Geräte-ID und einen Ampel-Badge für offene Nachreplikationen (`pending_copies > 0` → "wird nachrepliziert"), plus einen Admin-Override-Schalter (`GET`/`PUT /api/storage-service/guard-config`, `allow_degraded_start`). Wie bei `OcrSettings` gibt es **kein** Feld für das Ziel-Set selbst (Backends/Zugangsdaten sind reine Deployment-Konfiguration, `DMS_TARGETS`) und **keinen** Inline-Notfall-Schalter im Moment einer Startverweigerung — der Override ist eine proaktiv gesetzte Standing-Policy, die erst beim nächsten Neustart greift (Begründung: ADR 0017, derselbe Zero-Change-artige Deployment/Admin-UI-Split wie bei `ocrEnabled`, ADR 0016). Bei nicht erreichbarem Storage Service (z. B. weil ein Start gerade verweigert wurde) zeigt die Seite denselben erklärenden Leerzustand wie `OcrSettings`. **Seit P5c-S2**: jede Zeile hat zusätzlich einen Button "Datenträger-Wechsel akzeptieren" (`window.confirm`-Bestätigung, `POST /api/storage-service/guard-status/{target_id}/reidentify`) für den Korrekturmechanismus bei einem beabsichtigten, legitimen Geräte-Tausch — ersetzt die zuvor nötige direkte DB-Korrektur. **Seit Post-Roadmap Phase 22 Session 7** ([ADR 0092](../adr/0092-storage-target-metadata-editable.md)): die zuvor rein lesende "Object Lock"-Spalte (seit P7-S1) ist jetzt eine Checkbox (`PUT /api/storage-service/guard-status/{target_id}/config`), plus eine neue zweite Checkbox-Spalte "Aussonderungs-Ziel" (`role=archive`) — ein Klick wirkt sofort, ohne Neustart. Weiterhin **kein** Editor für das Ziel-Set selbst (Zugangsdaten/Struktur bleiben Deployment-Konfiguration, siehe ADR 0091/0092 "Begründung").

## Betriebsparameter des Storage Service (3.6, Post-Roadmap Phase 22 Session 6, [ADR 0091](../adr/0091-connector-operational-config-live-editable.md))

`StorageOperationalConfig` (`/storage-operational-config/`) — schlichtes Formular (Schreibstrategie-
Auswahl, Quorum-Anzahl, maximale Replikationsversuche) gegen `GET`/`PUT
/api/storage-service/operational-config`. Anders als `StorageGuard`s Admin-Override (wirkt erst beim
nächsten Neustart) wirkt eine Änderung hier **sofort** — gleiches Lade-/Speicher-Muster wie
`ShareLinkSettings`. Wie bei `StorageGuard` bleibt das Ziel-Set selbst (Zugangsdaten, `DMS_TARGETS`)
außerhalb dieser Seite (reine Deployment-Konfiguration, siehe ADR 0091 "Begründung").

## Signatur-Connectoren (3.10, Post-Roadmap Phase 22 Session 6, [ADR 0091](../adr/0091-connector-operational-config-live-editable.md))

`SignatureConfig` (`/signature-config/`) — **erste Admin-UI-Anbindung von `signature-service`
überhaupt**. Tabelle aller konfigurierten Connectoren (`GET /api/signature-service/signature-config`):
`id`/`type` rein lesend, je Zeile drei Checkboxen (SES/AES/QES) für `levels` — ein Klick ruft sofort
`PUT /api/signature-service/signature-config` mit nur diesem Connector auf und lädt neu. Client-seitig
blockiert (kein API-Aufruf), das letzte verbleibende Niveau eines Connectors abzuwählen (`levels` darf
laut Backend nie leer sein) — serverseitiges `422` bleibt die eigentliche Durchsetzung, das ist reine
UX-Vorwegnahme. Wie bei `StorageOperationalConfig` bleibt die Connector-*Liste* selbst (`id`/`type`,
`DMS_SIGNATURE_PROVIDERS`) außerhalb dieser Seite.

## Aufbewahrung & Löschregister (5.2/5.2a, seit P7-S1)

- **`RetentionSettings`** (`/retention-settings/`) bündelt zwei Konfigurationen des Document Service in einem Formular, gleiches Lade-/Speicher-Muster wie `UploadSettings`: `GET`/`PUT /api/document-service/retention-config` (`deletion_reason_required`, `reminder_lead_days`, leer = keine Erinnerung) und `GET`/`PUT /api/document-service/trash-config` (`restore_period_days`). Leerzustand bei nicht erreichbarem `document-service`, gleiches Muster wie die übrigen Konfigurationsseiten.
- **`DeletionRegister`** (`/deletion-register/`) ist eine reine Lese-Tabelle über `GET /api/document-service/deletion-register` — Zeitpunkt, Auslöser (`forced_deletion`/`trash_expiry`), Grund, auslösender Principal. Kein Editor, kein Löschen einzelner Einträge (Löschregister ist per Konzept 5.2a unveränderlich).

Legal-Hold-Verwaltung selbst (setzen/aufheben) findet **nicht** in der Admin-UI statt, sondern direkt am Dokument in der User-UI (`RetentionPanel`, siehe `docs/services/user-ui.md`) — konsistent damit, dass ein Legal Hold eine Aktion am konkreten Dokument ist, nicht eine installationsweite Einstellung.

## Aussonderung & Langzeitarchivierung (5.6, seit P7-S3)

- **`ArchivalTransfersView`** (`/archival-transfers/`) — reine Statustabelle gegen den neuen `archival-service` (`GET /api/archival-service/archival-transfers?status=...`, Status-Filter-Dropdown): Dokument-ID, Status (`pending`/`locked`/`copied`/`verified`/`released`/`dehydrated`/`failed`/`failed_permanent` seit **Post-Roadmap Phase 20 Session 7** mit deutscher Beschriftung + Badge, Fehlermeldung bei `failed`/`failed_permanent`), Archivformat, Verschlüsselt-Spalte, Zeitpunkte "Archiviert am"/"Dehydriert am". Ein "Zurückholen"-Button erscheint nur bei Status `released`/`dehydrated` (die einzigen mit verlässlicher Archivkopie), ruft nach Bestätigung `POST .../archival-transfers/{id}/retrieve` auf und lädt die Tabelle neu — gleiches Bestätigungs-/Reload-Muster wie `StorageGuard`s "Datenträger-Wechsel akzeptieren". Seit **Post-Roadmap Phase 20 Session 7** ([ADR 0083](../adr/0083-admin-ui-processing-failures-visibility.md)) zusätzlich ein "Erneut versuchen"-Button (beide Sektionen, Dokument UND Umlaufmappe) nur bei `failed_permanent`, ruft `POST .../retry` auf (bereits seit ADR 0078 serverseitig vorhanden) — bewusst ohne Bestätigungsdialog, da ein Neustart im Gegensatz zur Rückholung keine Live-Speicherkopie verändert. **Seit Post-Roadmap Phase 22 Session 1**: ein neues "Jetzt aussondern"-Formular am Kopf der Dokument-Sektion (Freitextfeld für die Dokument-ID + Button) ruft `POST /api/document-service/documents/{id}/archive-request` auf (neuer `requestDocumentArchive()`-API-Client, wraps `document-service`s bereits seit P5-S6/5.6 bestehenden manuellen Trigger, der `archive_after` auf jetzt setzt). Zeigt nach Erfolg einen Hinweistext statt die Transfer-Tabelle sofort neu zu laden — der Aufruf legt selbst noch **keine** `ArchivalTransfer`-Zeile an, das übernimmt erst `archival-service`s nächster Poll-Tick (Default stündlich).

## Verarbeitungsfehler-Sichtbarkeit (Post-Roadmap Phase 20 Session 7, [ADR 0083](../adr/0083-admin-ui-processing-failures-visibility.md))

- **`ProcessingFailuresView`** (`/processing-failures/`) — schließt die in ADR 0079/0080 entstandene Lücke: `notification-service`/`rendering-service`/`ocr-service` bekamen dort Retry/Backoff + `failed_permanent`, aber keine Admin-UI-Anbindung. Drei eigenständige Sektionen (kein gemeinsamer generischer Hook, gleiches "leichtgewichtige Duplikation statt Abstraktion"-Prinzip wie bei den Poll-Loops der zugehörigen Backend-Services), jede lädt ausschließlich `status=failed_permanent`-Datensätze des jeweiligen Service (`GET .../notifications?status=failed_permanent`, `GET .../renditions?status=failed_permanent`, `GET .../ocr-results?status=failed_permanent`) und bietet einen "Erneut versuchen"-Button je Zeile (`POST .../{id}/retry`, bereits seit ADR 0079/0080 serverseitig vorhanden). `rendering-service`/`ocr-service`s Listen-Endpunkte hatten `document_id` bislang als Pflichtparameter — für diese Session auf optional umgestellt (siehe unten), `notification-service`s `GET /notifications` hatte den nötigen `status`-Filter bereits.
- **Auslösung der Aussonderung selbst** (Objekttyp-Frist/manueller Trigger) passiert **nicht** hier, sondern über den erweiterten `ObjectTypeEditor` (`default_archive_after_days`/`archive_encryption_enabled`, s. u.) bzw. direkt am Dokument in der User-UI — diese Seite ist reine Beobachtung/Rückholung, kein "Jetzt aussondern"-Bedienelement (der automatische Poll-Loop von `archival-service` deckt den Regelfall bereits ab).
- **Rollen-Gate der Rückholung wird serverseitig durchgesetzt** (`archival-service`s `archive_retrieval_role`, Default `dms-admin`, aus dem gateway-injizierten `X-DMS-Roles`-Header) — die Admin-UI selbst blendet den Button nicht rollenabhängig aus, ein Klick ohne passende Rolle liefert `403`, im `error`-Text der Seite angezeigt.
- **Zweiter Abschnitt "Umlaufmappen-Aussonderung"** (`CaseArchivalSection`, gleicher `/archival-transfers/`-Slug, seit **P7-S3b**) — kein neuer Seiten-Slug, gleiches Mehr-Sektionen-Muster wie `RetentionSettings` (Dokumente + Ordner in einem Formular). Enthält zwei Teile: **(a)** ein Konfigurationsformular für `CaseArchivalConfig` (`GET`/`PUT /api/case-service/case-archival-config`) — installationsweite Aussonderungsfrist nach Abschluss + Verschlüsselungs-Toggel, kein Pendant im `ObjectTypeEditor`, da Umlaufmappen keine eigene `applies_to`-Kategorie haben (siehe `docs/services/case-service.md`). **(b)** eine Statustabelle (`GET /api/archival-service/case-archival-transfers?status=...`) mit Umlaufmappen-ID, Status (`pending`/`locked`/`packaged`/`verified`/`released`/`failed`), Verschlüsselt-Spalte, "Archiviert am". Ein "Paket herunterladen"-Button erscheint nur bei Status `released`, lädt das (ggf. serverseitig entschlüsselte) ZIP-Paket per `GET .../case-archival-transfers/{id}/package` als Blob herunter (`triggerBrowserDownload`, gleiches Muster wie `ReportsView`/`ForensicTraceView`) — **kein** Zurückschreiben auf ein Live-Ziel wie bei der Dokument-Rückholung, da eine Umlaufmappe keinen eigenen Live-Speicherplatz besitzt.

## Standardberichte (5.4a, seit P7-S2b)

`ReportsView` (`/reports/`) bündelt vier feste Berichtsabschnitte — Dokumentenaufkommen, offene Workflow-Aufgaben, Speicherverbrauch je Backend, Nutzeraktivität — plus einen fünften Abschnitt zur Verwaltung planbarer Berichtsläufe, alle gegen den neuen `reporting-service` (siehe `docs/services/reporting-service.md`). Jeder Berichtsabschnitt folgt demselben Muster: Filterformular (wo zutreffend), Tabelle, zwei Export-Buttons (`GET .../export?format=csv|pdf`, Blob-Download über denselben `triggerBrowserDownload`-Mechanismus wie beim Dokumenten-Download in der User-UI). Der Planungs-Abschnitt (`ReportScheduleSection`) legt/löscht `report_schedule`-Zeilen (Berichtstyp, Format, Häufigkeit, Empfänger-E-Mail) — der eigentliche Versand (Poll-Loop + E-Mail mit Downloadlink) läuft vollständig im `reporting-service`, diese Seite ist nur die Verwaltungsoberfläche dafür. Bewusst in der Admin-UI statt der User-UI — Systemauswertung/Nutzeraktivität ist ein administrativer Vorgang, gleiche Rollenteilung wie z. B. `MaintenanceBanner`.

## Forensik-Trace & Audit-Tiefe (5.4b, seit P7-S2c)

- **`ForensicTraceView`** (`/forensic-trace/`) — Filterformular (Akteur, Objekt-ID, Kategorie-Dropdown `view`/`download`/`change`/`delete`, Zeitfenster von/bis), Ergebnistabelle (Zeitpunkt/Ereignistyp/Kategorie/Akteur/Objekt/Service), Anomalie-Banner (rot, sobald `anomalies` im Response nicht leer ist), zwei Export-Buttons (gleiches `triggerBrowserDownload`-Muster wie `ReportsView`). Jede Abfrage (inkl. Export) sendet den aktuell angemeldeten Nutzernamen (`useAuth().user.username`) als `queried_by` mit — Pflichtparameter des `reporting-service`, da jede Trace-Abfrage dort selbst wieder auditiert wird (Selbst-Audit, 5.4b-Konzeptvorgabe). Vor der ersten Abfrage zeigt die Seite bewusst einen eigenen Hinweis statt eines automatischen initialen Ladevorgangs — anders als bei `ReportsView`s Standardberichten, die beim Öffnen sofort laden, würde ein Auto-Load hier bei jedem Seitenaufruf einen ungefilterten, potenziell sehr breiten Trace samt Selbst-Audit-Event auslösen.
- **`AuditTraceSettings`** (`/audit-trace-settings/`) — zwei Abschnitte gegen den Document Service: Basis-Konfiguration (zwei Checkboxen `log_viewed`/`log_downloaded`, Default beide an) und eine Tabelle der Rollen-Overrides (Rolle, zwei Tri-State-Selects Standard/An/Aus je Kategorie, Löschen-Button) mit Anlegeformular. Beide Selects im Anlegeformular tragen bewusst eigene, von den Basis-Checkbox-Labeln verschiedene `aria-label`-Texte (`overrideLogViewedLabel`/`overrideLogDownloadedLabel`) — identische Accessible Names an zwei Stellen derselben Seite hätten sowohl für Screenreader-Nutzer als auch für `getByLabelText`-basierte Tests eine nicht auflösbare Mehrdeutigkeit erzeugt (beim Schreiben der Tests entdeckt, siehe unten).

## Query-Konsole (6.1, seit P8-S1/P8-S2b)

`QueryConsoleView` (`/query-console/`) — zwei Abschnitte, beide gegen `query-service` (siehe `docs/services/query-service.md`):

- **Lesezugriff** (`QueryEventsSection`, seit P8-S1): Filterformular (Akteur, Objekt-ID, Ereignistyp, Zeitfenster von/bis), Ergebnistabelle (Zeitpunkt/Ereignistyp/Akteur/Objekt/Service). Anders als beim Forensik-Trace (`reporting-service`, ungefiltert) filtert `query-service` Ergebniszeilen aktiv nach Ordnerberechtigung der ausführenden Person — die Seite macht das transparent: nach jeder Abfrage zeigt ein Hinweis entweder `"{sichtbar} von {gesamt} Ereignissen sichtbar"` oder, für den aktivierten Superuser, einen eigenen "ungefiltert"-Hinweis. Kein `queried_by`-Parameter nötig (anders als beim Forensik-Trace) — `X-DMS-Principal` wird vom Gateway aus dem Bearer-Token injiziert. **Kein Freitext-SQL-Eingabefeld** — der `POST /query`-Pfad von `query-service` bleibt ohne installiertes Parser-Plugin (ADR 0031) ohnehin ungenutzt; nur die strukturierte Filter-API (`GET /query/events`) ist an die UI angebunden.
- **Manipulation** (`ManipulationSection`, seit **P8-S2b**) — nur sichtbar mit der feingranularen Capability `admin.query_console.manipulate` (zusätzlich zur bereits vorhandenen Sidebar-Gatung, Tiefen-Verteidigung — die eigentliche Durchsetzung bleibt serverseitig in `query-service`). Drei Unterabschnitte:
  - **Schutzschalter**: Statusanzeige (aktiv/bis wann/von wem) + Aktivieren-Formular (Minuten-Eingabe, Default 15)/Deaktivieren-Button — direkte Aufrufe der drei `query-service`-Schutzschalter-Endpunkte.
  - **Aktion**: `<select>` mit den drei bekannten `action_type`-Strings (hartkodierter Spiegel von `manipulation.py`s Katalog), dynamisches Parameter-Formular je Auswahl (`document.attribute_reset`: zwei Textfelder; `permission.role_assignment.delete`: eine Zahl; `object_type.update`: Zahl + Feld-Dropdown + rohes JSON-Textfeld für `value`, client-seitig geparst vor dem Absenden). Zwei-Schritt-Fluss: "Simulieren" (Dry-Run) zeigt Vorschau-Text + Kritisch-Badge und hält den `dry_run_token` im Komponentenzustand — erst danach erscheint "Ausführen"; jede Formularänderung verwirft den Token (erzwingt einen frischen Dry-Run vor jeder Ausführung). Ergebnis `"executed"` zeigt einen Erfolgstext, `"pending_approval"` einen Hinweistext und lädt die Genehmigungs-Tabelle neu.
  - **Ausstehende Genehmigungen**: `GET /approval-requests?status=pending` auf `permission-service` (bereits bestehende, generische Vier-Augen-Client-Funktion aus P6-S5, hier nur einmalig ohne `action_type`-Filter aufgerufen und client-seitig auf die drei bekannten Aktionstypen gefiltert — die API filtert `action_type` nur als einzelnen String, nicht als Liste), je Zeile Aktionstyp/Initiator/Zeitpunkt/rohe Parameter (JSON) + "Genehmigen"-Button (`approveApprovalRequest`, bereits generisch aus der Break-Glass-Anbindung, unverändert wiederverwendet). Kein Ablehnen-Button in dieser Session.

## Öffentlicher Freigabelink (4.2a, seit P14-S10)

`ShareLinkSettings` (`/share-link-settings/`) — ein Formular gegen `GET`/`PUT /api/document-service/share-link-config`: Checkbox "Freigabelinks erlauben" (`enabled`) + Zahlenfeld "Maximale Gültigkeitsdauer neuer Links (Tage)" (`max_validity_days`), gleiches Lade-/Speicher-/Leerzustand-Muster wie `OcrSettings`. Deaktivierung wirkt sofort auch auf bereits ausgegebene Links (siehe `docs/services/document-service.md`/[ADR 0047](../adr/0047-public-share-link-query-param-token-and-disable-semantics.md)), nicht nur auf Neuanlagen — diese Seite selbst greift dafür nicht ein, das ist serverseitige Logik in `document-service`.

## Stellvertretung bei Abwesenheit (4.4a, seit P14-S11)

`DelegationsAdmin` (`/delegations/`) — reine Tabellenübersicht gegen `GET /api/permission-service/delegations` (ungefiltert, alle Delegationen installationsweit): vertretene Person, Stellvertretung, Zeitfenster, Status (aktiv/noch nicht begonnen/abgelaufen/widerrufen), Widerrufen-Button nur für aktive Zeilen. Anlegen ist bewusst kein Admin-UI-Feature (Selbstverwaltung, Konzept-Wortlaut "Eine Person kann ... hinterlegen" — siehe `docs/services/user-ui.md`s `DelegationsPane`); diese Seite bildet nur den zweiten, im Konzept vorgesehenen Widerrufsweg ab ("... oder einer berechtigten Admin-Rolle beendet werden"), serverseitig durchgesetzt über `delegation_revoke_admin_role` in `permission-service`, siehe [ADR 0048](../adr/0048-delegation-lives-in-permission-service-no-task-assignee-retrofit.md).

## Konfigurationspakete (14.1, seit P17-S1)

`ConfigPackages` (`/config-packages/`) — **erste Admin-UI-Anbindung von `config-service`
überhaupt** (bis dahin ausschließlich CLI/rohe API/Fleet-Agent, siehe
`docs/services/config-service.md`). Ablauf:

1. **Paket-Datei laden**: `<input type="file">`, clientseitig per `FileReader` (bewusst nicht
   `File.text()` — Web-Standard, aber in der hier verwendeten jsdom-Testumgebung nicht
   implementiert, siehe Komponentenkommentar) als JSON geparst. Ungültiges JSON/fehlendes
   `schema_version`-Feld → Fehlermeldung, kein Absturz.
2. **Manifest/Kategorien anzeigen**: Name/Version/Kompatibilitätsspanne/Beschreibung/Herkunft/
   Lizenz aus `document.manifest`, falls vorhanden (optional, siehe `config-service`), plus eine
   Liste der im Dokument tatsächlich vorhandenen Kategorien samt Eintragsanzahl.
3. **Vorschau** (`compareConfig`, `POST /api/config-service/config/compare` ohne `base` — zieht
   automatisch den eigenen aktuellen Live-Export als Basisinstanz heran, 7.5/P14-S1): zeigt je
   Kategorie, was neu wäre (`only_in_compare`), was sich ändern würde (`differing`) und was
   unverändert im aktuellen System bliebe (`only_in_base` — Import ist additiv/Upsert, hier wird
   nichts gelöscht).
4. **Anwenden** (`importConfig`, `POST /api/config-service/config/import`): Ergebnistabelle mit
   Neu-angelegt/Aktualisiert/Übersprungen/Fehlern je Kategorie (`CategoryResult`).
5. **Aktuelle Konfiguration exportieren**: naheliegende Ergänzung, `GET /api/config-service/
   config/export` als JSON-Datei-Download (`Blob`/`URL.createObjectURL`) — nicht Teil des
   ursprünglichen Sessionsumfangs, aber ohne Mehraufwand ergänzt, da derselbe Client-Code für die
   Vorschau ohnehin bereits einen Live-Export lädt.

Sidebar-Sichtbarkeit gegated über `requiresCapability: "admin.object_config"` (dieselbe Capability
wie `config-service`s eigenes Import-Gate) — kein zusätzlicher `RequireCapability`-Wrapper auf der
Seite selbst, folgt damit dem häufigeren, bereits etablierten Muster in diesem Projekt (siehe
"Autorisierung" unten). Details/Begründung der Backend-Änderungen (Manifest-Feld, neue
`realm_roles`-Kategorie, Trennung von `config/import`/`config/fleet-import` am Gateway) siehe
[ADR 0058](../adr/0058-konfigurationspakete-manifest-realm-roles-and-gateway-import-route-split.md).

## Mehrfachinstallationen (Konzept 3a/8, seit P4-S5)

Die Admin-UI kann mehrere vollständig unabhängige DMS-Installationen verwalten, ohne sich bei jedem Wechsel neu anmelden zu müssen — siehe [ADR 0008](../adr/0008-admin-ui-multi-installation-sessions.md) für die technische Begründung. Kurzfassung:

- Installationsliste (`{id, name, gatewayBaseUrl}`) rein clientseitig in `localStorage`, verwaltet über `InstallationManager` (`/installations/`) und `useInstallation()` (`lib/installation-context.tsx`).
- `InstallationSwitcher` in der Kopfzeile wechselt die aktive Installation — bleibt ausgeblendet, solange nur eine Installation konfiguriert ist.
- **Eigene Sitzung je Installation**: `auth-context.tsx` speichert Tokens unter `dms.tokens.<installationId>` statt eines einzigen globalen Schlüssels. Ein Wechsel zu einer bereits einmal angemeldeten Installation erfordert keine erneute Anmeldung, solange deren Sitzung noch gültig ist; eine neue, noch nie angemeldete Installation zeigt beim Wechsel den Login.
- **Kein Single-Sign-on über Installationsgrenzen hinweg** — bewusst, entspricht der vollständigen Isolation aus Konzept 3a.
- `lib/api.ts`s Gateway-Adresse ist seit dieser Session eine mutable Modulvariable (`setGatewayBaseUrl()`) statt einer festen Konstante, vom `InstallationProvider` bei jedem Wechsel synchron gesetzt.

## Theming (Konzept 8, seit P4-S6)

`src/lib/theme-context.tsx` (`ThemeProvider`/`useTheme()`) — identisches Muster wie in der User-UI (bewusst dupliziert statt geteilt, ADR 0006), umschaltbar über den `ThemeSwitcher` in der Kopfzeile. Geräteübergreifend am Nutzerkonto der **aktiven Installation** gespeichert (`GET/PUT /api/auth-service/me/preferences`, `accessToken` kommt aus dem installationsbezogenen `AuthProvider`, ADR 0008), siehe [ADR 0009](../adr/0009-cross-ui-theming-profile-persistence.md). Der `localStorage`-Cache-Schlüssel (`dms.theme`) ist bewusst **nicht** installationsspezifisch — ein Installationswechsel zeigt kurzzeitig noch die zuletzt gecachte Theme-Wahl, bis die neue Installation ihre eigene Präferenz nachgeladen hat (siehe ADR 0009 "Konsequenzen").

## Anbindung an das Backend

Ausschließlich über das API-Gateway der jeweils **aktiven Installation** (3.5, `/api/{service_type}/{path}`):

| Bereich | Gateway-Aufrufe |
|---|---|
| Login/Identität | `POST /api/auth-service/login`, `GET /api/auth-service/me` |
| Nutzer | `GET/POST /api/auth-service/users`, `DELETE /api/auth-service/users/{id}` |
| Rollen | `GET/POST /api/permission-service/roles` |
| Teamspaces-Admin-Übersicht (seit **Post-Roadmap Phase 22 Session 5**) | `GET /api/teamspace-service/admin/teamspaces` |
| Gruppen (seit **Post-Roadmap Phase 22 Session 2**) | `GET/POST /api/permission-service/groups`, `DELETE .../{id}`, `GET/POST /api/permission-service/groups/{id}/members`, `DELETE .../{id}/members/{principal_id}` |
| Rollenzuweisungen | `GET/POST /api/permission-service/role-assignments`, `DELETE .../{id}` |
| Vier-Augen-Einstellungen (seit **Post-Roadmap Phase 22 Session 3**) | `GET /api/permission-service/approval-config`, `PUT .../{action_type}` |
| Objekttypen | `GET/POST/PUT/DELETE /api/object-type-service/object-types`, `GET/PUT/DELETE .../object-types/{id}/layouts/{purpose}` (seit P5b-S3) — seit **P7-S3** zusätzlich `default_archive_after_days`/`archive_encryption_enabled` im Create/Update-Payload (5.6) |
| Registry | `GET /api/registry-service/instances` |
| Theme-Präferenz | `GET/PUT /api/auth-service/me/preferences` (seit P4-S6) |
| OCR-Einstellungen | `GET/PUT /api/ocr-service/config` (seit P5b-S5) |
| Speicher-Wächter | `GET/PUT /api/storage-service/guard-config`, `GET /api/storage-service/guard-status` (seit P5b-S6), `POST /api/storage-service/guard-status/{target_id}/reidentify` (seit P5c-S2), `PUT /api/storage-service/guard-status/{target_id}/config` (seit **Post-Roadmap Phase 22 Session 7**) |
| Betriebsparameter (Storage, seit **Post-Roadmap Phase 22 Session 6**) | `GET/PUT /api/storage-service/operational-config` |
| Signatur-Connectoren (seit **Post-Roadmap Phase 22 Session 6**) | `GET/PUT /api/signature-service/signature-config` |
| Not-Shutdown / Wartungsmodus | `GET /api/permission-service/maintenance-mode` (seit P6-S6, `MaintenanceBanner` + `SuperuserBreakGlass`), `POST /api/permission-service/maintenance-mode/trigger`, `POST /api/permission-service/maintenance-mode/lift` (beide seit P6-S6, `SuperuserBreakGlass`) |
| Aufbewahrung & Löschregister | `GET/PUT /api/document-service/retention-config`, `GET/PUT /api/document-service/trash-config`, `GET /api/document-service/deletion-register` (alle seit P7-S1, `RetentionSettings`/`DeletionRegister`); seit **P7-S1b** zusätzlich dieselben drei gegen `folder-service` (eigene, unabhängige Configs) |
| Standardberichte | `GET /api/reporting-service/reports/{document-volume,open-workflow-tasks,storage-usage,user-activity}` + `.../export?format=csv\|pdf`, `GET/POST/DELETE /api/reporting-service/report-schedules`, `GET /api/reporting-service/report-runs/{id}/download` (alle seit P7-S2b, `ReportsView`) |
| Forensik-Trace & Audit-Tiefe | `GET /api/reporting-service/forensic-trace` + `.../export?format=csv\|pdf` (seit P7-S2c, `ForensicTraceView`), `GET/PUT /api/document-service/audit-trace-config`, `GET/PUT/DELETE /api/document-service/audit-trace-role-overrides/{role}` (seit P7-S2c, `AuditTraceSettings`) |
| Aussonderung & Archivierung | `GET /api/archival-service/archival-transfers?status=...`, `POST .../archival-transfers/{id}/retrieve` (beide seit P7-S3, `ArchivalTransfersView`); seit **P7-S3b** zusätzlich `GET /api/archival-service/case-archival-transfers?status=...`, `GET .../case-archival-transfers/{id}/package`, `GET`/`PUT /api/case-service/case-archival-config` (`CaseArchivalSection`); seit **Post-Roadmap Phase 20 Session 7** zusätzlich `POST .../archival-transfers/{id}/retry`, `POST .../case-archival-transfers/{id}/retry`; seit **Post-Roadmap Phase 22 Session 1** zusätzlich `POST /api/document-service/documents/{id}/archive-request` |
| Verarbeitungsfehler | `GET /api/notification-service/notifications?status=failed_permanent`, `POST .../notifications/{id}/retry`, `GET /api/rendering-service/renditions?status=failed_permanent`, `POST .../renditions/{id}/retry`, `GET /api/ocr-service/ocr-results?status=failed_permanent`, `POST .../ocr-results/{id}/retry` (alle seit **Post-Roadmap Phase 20 Session 7**, [ADR 0083](../adr/0083-admin-ui-processing-failures-visibility.md), `ProcessingFailuresView`) |
| Query-Konsole | `GET /api/query-service/query/events?actor=&subject=&event_type=&since=&until=` (seit **P8-S1**, `QueryConsoleView`); seit **P8-S2b** zusätzlich `GET/POST /api/query-service/manipulation-mode/{status,activate,deactivate}`, `POST /api/query-service/manipulate/{dry-run,execute}`, `GET /api/permission-service/approval-requests?status=pending`, `POST /api/permission-service/approval-requests/{id}/approve` (letztere zwei bereits bestehende Endpunkte, wiederverwendet) |
| Konfigurationspakete | `GET /api/config-service/config/export`, `POST /api/config-service/config/compare`, `POST /api/config-service/config/import` (alle seit **P17-S1**, `ConfigPackages`) |

## Auth-Zustand

`src/lib/auth-context.tsx` — installationsbezogen seit P4-S5 (siehe oben), sonst wie die User-UI: `localStorage`-Tokens, proaktiver Refresh — bewusst dupliziert statt geteilt (ADR 0006: keine gemeinsame Fachlogik zwischen unabhängig deploybaren Frontend-Apps). Lädt seit **P6-S5** zusätzlich `permissions: string[]` (`getEffectivePermissions`, Permission Service) direkt nach `user` — systemeigene Domain-Admin-Capabilities (4.6), bewusst getrennt von `user.realm_roles` (Keycloak), siehe [ADR 0023](../adr/0023-superuser-breakglass-and-domain-admin-accounts.md).

## Internationalisierung (Konzept 8, seit P4-S3)

Wie die User-UI: `src/i18n/de.json` + `useI18n()` (siehe [ADR 0007](../adr/0007-frontend-i18n-preparation.md)). Eigenes Wörterbuch, da Admin-UI-Begriffe (Nutzerverwaltung, Objekttyp-Editor, Registry, Installationen) sich vollständig von den User-UI-Begriffen unterscheiden.

## Autorisierung

**Seit P6-S5 für einen ersten Fall durchgesetzt** (4.6): `/users/` prüft die Capability `admin.user_management` — `AdminSidebar` blendet den Nav-Eintrag ohne diese Capability aus, `RequireCapability` (Geschwister von `RequireAuth`) schützt zusätzlich die Route selbst (Tiefen-Verteidigung gegen direkt aufgerufene URLs), und der eigentliche Backend-Endpunkt (`auth-service` `/users`) setzt es unabhängig vom UI-Zustand durch. Für **alle anderen** administrativen Bereiche (Objekttyp-Editor, Registry, Installationen, OCR-/Upload-/Kennzeichen-Einstellungen, Speicher-Wächter) gilt weiterhin die bisherige Lücke: Das Gateway prüft nur, dass ein gültiger Bearer-Token vorliegt, nicht, ob der Principal zu der jeweiligen administrativen Aktion berechtigt ist — jeder erfolgreich angemeldete Nutzer kann sie aktuell vollständig nutzen. Die zugrunde liegenden Domain-Admin-Rollen dafür sind zwar bereits in `permission-service` geseedet (siehe `docs/services/permission-service.md`), aber ohne zugeordnetes Konto und ohne Enforcement am jeweiligen Endpunkt — folgt jeweils mit der künftigen Retrofit-Session der betreffenden Domäne. **Ausnahme seit P17-S1**: `/config-packages/` ist der zweite Fall mit tatsächlicher serverseitiger Durchsetzung — `POST /api/config-service/config/import` selbst verlangt `admin.object_config` (siehe `docs/services/config-service.md`); die Admin-UI-Seite blendet den Sidebar-Eintrag ohne diese Capability nur aus (`requiresCapability`), verzichtet aber wie die Mehrheit der übrigen capability-gegateten Seiten (z. B. `/query-console/`) bewusst auf einen zusätzlichen `RequireCapability`-Wrapper um die Route selbst.

**Seit P6-S6 zusätzlich für Not-Shutdown (4.8)**: `SuperuserBreakGlass` blendet das Auslösen-Formular nur ein, wenn `permissions` (aus `auth-context.tsx`) die Capability `system.not_shutdown.trigger` enthält — rein clientseitige UX-Vorwegnahme, die eigentliche Durchsetzung passiert am Permission Service (`403` ohne Capability). Der Aufheben-Button ist zusätzlich an eine zweite Bedingung geknüpft, die nicht rein rollenbasiert ist: er erscheint nur, wenn der aktuell angemeldete Principal (`user.sub`) mit `status.principal_id` (dem aktiven Superuser) übereinstimmt — jede andere Person mit `system.not_shutdown.trigger` sieht den Button nicht, obwohl sie den Wartungsmodus auslösen dürfte (Auslösen und Aufheben sind bewusst unterschiedliche Berechtigungen, 4.8).

## Build & Auslieferung

Zweistufiges Docker-Image (`apps/admin-ui/Dockerfile`), identisch zur User-UI. `NEXT_PUBLIC_GATEWAY_BASE_URL` als Build-Arg (Startwert der "Lokal"-Installation), überschreibbar über `ADMIN_UI_GATEWAY_BASE_URL` in `infra/.env`. Port `3001` (User-UI: `3000`). Weitere Installationen werden zur Laufzeit über `/installations/` hinzugefügt, nicht über einen erneuten Build.

## Tests

- `npm run typecheck` / `npm run lint` / `npm run build`.
- `npm test` (Vitest + Testing Library, **204 Tests** — seit **Post-Roadmap Phase 22 Session 7** (siehe
  "Speicher-Wächter" oben): `storage-guard.test.tsx` um drei Tests erweitert (Umschalten des
  Object-Lock-Modus inkl. Reload, Umschalten der Aussonderungs-Rolle, Fehleranzeige bei `422`); davor
  201 — seit **Post-Roadmap Phase 22 Session 6** (siehe
  "Betriebsparameter des Storage Service"/"Signatur-Connectoren" oben): neue Testdatei
  `storage-operational-config.test.tsx` (4 Tests: Laden/Anzeigen, Unreachable-Zustand, Speichern
  geänderter Werte, Fehleranzeige bei `422`) für `StorageOperationalConfig`, neue Testdatei
  `signature-config.test.tsx` (6 Tests: Auflisten inkl. Niveau-Checkboxen, Leerzustand,
  Unreachable-Zustand, Umschalten inkl. Reload, kein Aufruf beim Versuch das letzte Niveau
  abzuwählen, Fehleranzeige bei `422`) für `SignatureConfig`; davor 191 — seit **Post-Roadmap Phase 22 Session 5** (siehe
  "Teamspaces-Admin-Übersicht" oben): neue Testdatei `teamspaces-admin.test.tsx` (4 Tests: Auflisten
  inkl. Teamspaces, in denen der Aufrufer selbst kein Mitglied ist, Leerzustand, Unreachable-Zustand,
  Fehleranzeige bei fehlender Capability) für die neue `TeamspacesAdmin`, plus zwei neue
  `admin-sidebar.test.tsx`-Tests für die Capability-Gatung des neuen Sidebar-Eintrags
  "Team-Arbeitsbereiche"; davor 185 — seit **Post-Roadmap Phase 22 Session 3** (siehe
  "Vier-Augen-Einstellungen" oben): neue Testdatei `approval-settings.test.tsx` (6 Tests: Leerzustand,
  Unreachable-Zustand, Auflisten sortiert inkl. `required_permission`/Status, Umschalten inkl. Erhalt von
  `required_permission`, Anlegen eines neuen Aktionstyps, Fehleranzeige beim Umschalten) für die neue
  `ApprovalSettings`; davor 179 — seit **Post-Roadmap Phase 22 Session 2** (siehe
  "Gruppen-Verwaltung" oben): vier neue Tests in `user-management.test.tsx` (Leerzustand ohne Gruppen,
  Anlegen inkl. Reload, Auflisten + Löschen, Aufklappen inkl. Mitgliederliste laden/Mitglied
  hinzufügen/entfernen); davor 175 — seit **Post-Roadmap Phase 22 Session 1**
  (siehe "Aussonderung & Langzeitarchivierung" unten): zwei neue Tests in `archival-transfers.test.tsx`
  für das neue "Jetzt aussondern"-Formular (Erfolgsfall inkl. Hinweistext, Fehleranzeige bei
  `ApiError`); davor 173 — seit **Post-Roadmap Phase 20 Session 7**
  ([ADR 0083](../adr/0083-admin-ui-processing-failures-visibility.md)) neun neue Tests: drei in
  `archival-transfers.test.tsx` (kein Neustart-Button bei nur `failed`, Neustart bei `failed_permanent`
  für Dokument- UND Case-Sektion inkl. Reload), neue Testdatei `processing-failures.test.tsx` (6 Tests:
  Laden aller drei Sektionen mit dem `status=failed_permanent`-Filter, Leerzustände je Sektion,
  Unreachable-Zustand, je ein Neustart-Testfall pro Sektion) für die neue `ProcessingFailuresView` gegen
  `notification-service`/`rendering-service`/`ocr-service`; davor 164 — seit **P17-S3** drei neue Tests: zwei in
  `user-management.test.tsx` (Anlegen einer Rollenzuweisung inkl. Reload bei `status="created"`,
  Vier-Augen-Hinweis ohne Reload bei `status="pending_approval"`, `permission.role_assignment.create`)
  und einer in `config-packages.test.tsx` (Vier-Augen-Hinweis ohne Ergebnistabelle bei
  `status="pending_approval"` für `config.import`) — beide Komponenten konsumieren seit P17-S3 ein
  Status-Envelope statt des vorherigen flachen Objekts, siehe `docs/services/permission-service.md`/
  `config-service.md`; davor 161 — seit **P17-S1** neue Testdatei `config-packages.test.tsx` (7 Tests: Hinweistext, Laden einer Paket-Datei inkl. Manifest-/Kategorienanzeige, Fehleranzeige bei ungültigem JSON, Hinweis bei fehlendem Manifest, Vorschau via `compareConfig` inkl. Delta-Anzeige, Anwenden via `importConfig` inkl. Ergebnistabelle, Export der aktuellen Konfiguration als Download) für die neue `ConfigPackages`-Komponente — Datei-Upload über `userEvent.upload` + `FileReader` (siehe Komponentenkommentar zu `File.text()` in jsdom); seit **P14-S11** neue Testdatei `delegations-admin.test.tsx` (5 Tests: Leerzustand, Unreachable-Zustand, Auflistung inkl. Status-Badges und genau einem Widerrufen-Button für die aktive Zeile, Widerrufen nach Bestätigung, kein Widerruf bei abgelehnter Bestätigung) für die neue `DelegationsAdmin` gegen den Permission Service; davor 147, vorher 135 — seit **P14-S10** neue Testdatei `share-link-settings.test.tsx` (3 Tests: Laden/Anzeigen der Konfiguration, Leerzustand bei nicht erreichbarem `document-service`, Speichern geänderter Werte) für die neue `ShareLinkSettings` gegen den Document Service; davor 135, vorher 127 — seit **P8-S2b** `query-console-view.test.tsx` um den Manipulations-Abschnitt erweitert (8 neue Tests: Sichtbarkeit hinter `admin.query_console.manipulate`, Schutzschalter aktivieren/deaktivieren, Dry-Run→Execute-Fluss für eine nicht-kritische Aktion, Kritisch-Badge + Pending-Approval-Ergebnis für eine kritische Aktion, JSON-Parse-Fehler im `object_type.update`-Wertfeld, Genehmigungs-Tabelle rendern + Genehmigen-Aufruf, Leerzustand); davor 127, vorher 120 — seit **P8-S1** neue Testdatei `query-console-view.test.tsx` (5 Tests: Hinweis vor erster Abfrage, Abfrage inkl. Rendern der Ergebniszeilen und des "N von M sichtbar"-Filterhinweises, Leerzustand mit ausgeblendetem Filterhinweis, Superuser-Hinweis statt Filterhinweis, Fehleranzeige) für die neue `QueryConsoleView` gegen den neuen `query-service`, plus zwei neue `admin-sidebar.test.tsx`-Tests für die Capability-Gatung des neuen Sidebar-Eintrags "Query-Konsole"; davor 120, vorher 116 — seit **P7-S3b** `archival-transfers.test.tsx` um `CaseArchivalSection` erweitert (4 neue Tests: Konfiguration + Leerzustand, Statustabelle inkl. bedingtem Download-Button nur bei `released`, Paket-Download inkl. Blob-Mock, Konfiguration speichern); davor 116, vorher 107 — seit **P7-S3** eine neue Testdatei `archival-transfers.test.tsx` (6 Tests: Liste inkl. Status-/Format-Anzeige, Leerzustand, Unreachable-Zustand, kein Rückholen-Button bei `pending`, erfolgreiche Rückholung nach Bestätigung inkl. Reload, keine Rückholung bei abgelehnter Bestätigung) für die neue `ArchivalTransfersView` gegen den neuen `archival-service`, plus drei neue `ObjectTypeEditor`-Tests für die Aussonderungs-Felder (`default_archive_after_days`/`archive_encryption_enabled` sichtbar für beide `applies_to`-Werte, Absenden beim Anlegen, Laden bestehender Werte beim Bearbeiten); davor 107, vorher 96 — seit **P7-S2c** zwei neue Testdateien `forensic-trace-view.test.tsx` (6 Tests: Hinweis vor erster Abfrage, Abfrage mit `queried_by`=aktueller Nutzername und Rendern kategorisierter Treffer, Leerzustand, Anomalie-Banner, Fehleranzeige, CSV-Export) und `audit-trace-settings.test.tsx` (5 Tests: Laden/Anzeigen der Basis-Konfiguration mit Default "beide an", Leerzustand ohne Overrides, Speichern geänderter Basis-Werte, Anzeigen/Löschen eines Overrides — hier bewusst mit `within(row)` statt eines pageweiten `getByText`, da "An"/"Standard" sowohl in der Override-Tabelle als auch in den Tri-State-Selects des Anlegeformulars vorkommen —, Anlegen eines neuen Overrides); davor 96, vorher 87 — seit **P7-S2b** neue Testdatei `reports-view.test.tsx` (9 neue Tests: Leerzustände aller vier Berichte + Planungsliste, Rendern echter Zeilen je Berichtstyp, Fehleranzeige bei fehlgeschlagenem Laden, CSV-Export-Aufruf inkl. `triggerBrowserDownload`, Planung anlegen/löschen) für die neue `ReportsView` gegen den neuen `reporting-service`; davor 87, vorher 86 — seit **P7-S1b** `RetentionSettings` in zwei unabhängige Sektionen aufgeteilt (Dokumente/Ordner, je eigener Lade-/Speicher-/Fehlerzustand) und `DeletionRegister` führt Dokument- und Ordner-Löschregister anzeigeseitig zusammen; davor 86 Tests, vorher 77, 9 neu — 3 neue `ObjectTypeEditor`-Tests für die Aufbewahrungs-Felder plus zwei neue Testdateien `RetentionSettings`/`DeletionRegister`, siehe P7-S1-Ergänzungen unten): API-Client (inkl. Routing über die aktiv gesetzte Gateway-Adresse), `AuthProvider` (Login/Logout/Session-Wiederherstellung, seit P4-S5 zusätzlich: Sitzungsisolation zwischen zwei Installationen, kein erneutes Login beim Zurückwechseln, seit P6-S5 zusätzlich: `permissions` wird nach Login/Session-Restore geladen und exponiert), `InstallationProvider` (Bootstrap, Hinzufügen/Wechseln/Entfernen, Schutz vor Entfernen der letzten Installation, Persistenz), `ThemeProvider` (seit P4-S6: Default `auto`, `data-theme`-Attribut, `localStorage`-Cache-Wiederherstellung, `setTheme`-Persistenz), `AdminSidebar` (Gruppen auf-/zuklappen inkl. Persistenz, seit P6-S5 zusätzlich: `/users/`-Eintrag wird ohne `admin.user_management`-Capability ausgeblendet), `InstallationManager`/`InstallationSwitcher`, `UserManagement`, `RegistryOverview`, seit P6-S5 zusätzlich `SuperuserBreakGlass` (Status active/inactive inkl. Ablaufzeitpunkt, Leerzustand bei nicht erreichbarem `auth-service`, Aktivierung anfordern und Genehmigen jeweils als der aktuell angemeldete Principal), seit **P6-S6** zusätzlich in `SuperuserBreakGlass` (Not-Shutdown-Formular ohne die Capability `system.not_shutdown.trigger` ausgeblendet, Auslösen als aktuell angemeldeter Principal, Aufheben-Button nur für den aktiven Superuser selbst sichtbar) sowie neu `MaintenanceBanner` (kein Rendering bei inaktivem Wartungsmodus, Anzeige bei aktivem Wartungsmodus, bleibt still bei unerreichbarem Permission Service), seit P5b-S3 zusätzlich `ObjectTypeEditor` (strukturierte Attribut-Erfassung, Label-getriebene Initial-Layout-Persistenz, Bearbeiten-Modus inkl. Erhalt von `naming_constraints`/`conditions`, Icon-Feld nur für Ordnerklassen, seit P5e-S3 zusätzlich Kennzeichen-Format-/Anzeige-Override-Feld nur für Dokumentklassen inkl. Laden bestehender Werte) und `LayoutDesigner` (generiertes vs. gespeichertes Layout, Zeilen-/Feldoperationen, Speichern/Zurücksetzen), seit P5b-S5 zusätzlich `OcrSettings` (lädt/zeigt die aktuelle Konfiguration, Speichern geänderter Werte, Leerzustand bei nicht erreichbarem `ocr-service`, seit P5d-S1 zusätzlich die Content-Type-Positivliste), seit P5d-S1 zusätzlich `UploadSettings` (lädt/zeigt die Format-Whitelist, Speichern geänderter Werte, Leerzustand bei nicht erreichbarem `document-service`), seit P5b-S6 zusätzlich `StorageGuard` (Status-Tabelle inkl. Nachreplikations-Badge, Speichern des Admin-Override-Schalters, Leerzustand bei nicht erreichbarem `storage-service`), seit P5c-S2 zusätzlich zwei `StorageGuard`-Tests für den "Datenträger-Wechsel akzeptieren"-Button (Bestätigung akzeptiert → Reload zeigt neue Geräte-ID, Bestätigung abgelehnt → kein API-Aufruf), seit P5e-S3 zusätzlich `KennzeichenSettings` (lädt/zeigt den globalen Standard, Speichern, Leerzustand bei nicht erreichbarem `object-type-service`), seit **P7-S1** zusätzlich `ObjectTypeEditor` (Aufbewahrungsfrist-/Löschgrund-Pflicht-Felder für beide `applies_to`-Werte sichtbar, inkl. Laden bestehender Werte), `RetentionSettings` (lädt/zeigt beide Configs, Speichern, Leerzustand bei nicht erreichbarem `document-service`) und `DeletionRegister` (Leerzustand, Zeilen mit aufgelösten Auslöser-Labels, Leerzustand bei nicht erreichbarem `document-service`) — Netzwerkschicht gemockt, gleiche Begründung wie bei der User-UI. `matchMedia` wird in `tests/setup.ts` gepolyfillt, da jsdom es nicht implementiert.
- **Kein Browser in dieser Entwicklungsumgebung verfügbar** (siehe `docs/services/user-ui.md`) — jeder Gateway-Aufruf der Admin-UI wurde einzeln per `curl` gegen den echten laufenden Compose-Stack nachvollzogen (seit P4-S6 zusätzlich: `GET/PUT /me/preferences` inkl. 422 bei ungültigem Theme-Wert; seit P5b-S3 zusätzlich: die vom `ObjectTypeEditor` beim Anlegen mit abweichenden Anzeigenamen gesendete dreifache `PUT .../layouts/{purpose}`-Sequenz sowie ein `PUT /object-types/{id}` mit unverändert durchgereichten `naming_constraints`/`conditions`, beides 1:1 nachgestellt; seit P5b-S5 zusätzlich `GET/PUT /api/ocr-service/config`; seit P5b-S6 zusätzlich `GET/PUT /api/storage-service/guard-config` und `GET /api/storage-service/guard-status` gegen einen echten simulierten Datenträger-Wechsel; seit P5c-S2 zusätzlich `POST /api/storage-service/guard-status/{target_id}/reidentify` gegen einen echten, ohne Neustart akzeptierten Datenträger-Wechsel; seit P5e-S3 zusätzlich `GET/PUT /api/object-type-service/kennzeichen-config` sowie ein vollständiger Ende-zu-Ende-Durchlauf über das Gateway mit echtem Login: Objekttyp mit `kennzeichen_format` anlegen, Dokument mit gefälschtem Client-`Kennzeichen` erstellen lassen (Server-Wert gewinnt), `PATCH` ohne `dms-admin`-Rolle → `403`; seit **Post-Roadmap Phase 20 Session 7** zusätzlich direkt gegen die neu `document_id`-optionalen `GET /api/rendering-service/renditions?status=failed_permanent`/`GET /api/ocr-service/ocr-results?status=failed_permanent` verifiziert — beide lieferten echte, aus früheren Live-Verifikationen dieser Phase stammende `failed_permanent`-Datensätze über mehrere Dokumente hinweg zurück, bestätigt die dokumentübergreifende Filterung mit echten statt synthetischen Daten; `GET /api/notification-service/notifications?status=failed_permanent` lieferte korrekt eine leere Liste; die neue `/processing-failures/`-Route wird vom `admin-ui`-Container ausgeliefert). Das Multi-Installation-Verhalten, das Theme-Umschalten und die neuen geführten Formulare/der Layout-Designer/die OCR-Einstellungsseite/der Speicher-Wächter-Statusblock/die Kennzeichen-Felder/die neue `ProcessingFailuresView` selbst (Zeilen-/Feld-Interaktionen, Badges, bedingte Felder) sind rein clientseitig und wurden **nur über die Vitest-Komponententests verifiziert, nicht visuell im Browser** — an den Nutzer explizit als Einschränkung kommuniziert.

## Offene Punkte

- **Autorisierung nur für `/users/` durchgesetzt** (s. o., seit P6-S5) — alle anderen administrativen Bereiche bleiben ungated, nach wie vor ein wichtiger offener Punkt; die neuen `/retention-settings/`/`/deletion-register/`-Seiten (P7-S1) und `/archival-transfers/` (P7-S3) sind davon keine Ausnahme — Letztere blendet den Rückholen-Button unabhängig von der Rolle immer ein, das serverseitige `403` von `archival-service` ist die tatsächliche Durchsetzung.
- ~~Kein "Jetzt aussondern"-Bedienelement (5.6, seit P7-S3) — `ArchivalTransfersView` ist reine Beobachtung/Rückholung; ein manueller Trigger müsste über `document-service`s `POST /documents/{id}/archive-request` erfolgen, dafür gibt es noch keine Admin-UI-Anbindung~~ — **behoben in Post-Roadmap Phase 22 Session 1** (siehe "Aussonderung & Langzeitarchivierung" oben): neues Formular ruft den Endpunkt per Dokument-ID auf. Weiterhin **kein** Button direkt am Dokument in der User-UI (die Admin-UI kennt keine Dokumentenliste/-suche, nur die freie ID-Eingabe) — ein möglicher künftiger Ausbau.
- **Not-Shutdown-Bedienung (4.8, seit P6-S6) rein clientseitig für Sichtbarkeit, nicht Absicherung** — `SuperuserBreakGlass` blendet Formular/Button nur aus, die eigentliche Durchsetzung passiert ausschließlich am Permission Service; kein neuer Nav-Eintrag, da inhaltlich an die bestehende Break-Glass-Seite gekoppelt (4.8 verweist selbst auf 4.6).
- `naming_constraints`/`conditions` haben weiterhin kein geführtes UI-Formular (werden beim Bearbeiten nur unverändert erhalten) — Freitext-/JSON-Bearbeitung dieser beiden Felder ist nicht Teil von P5b-S3 und bleibt ein offener Punkt für eine spätere Session.
- Kein Rückwirkungs-Check, wenn beim Bearbeiten ein Attribut umbenannt/entfernt wird, das bereits in einem gespeicherten Layout-Override referenziert ist (gleiche Einschränkung wie ADR 0014) — der Objekttyp-Editor fasst bestehende Layouts bewusst nicht an, das nächste Öffnen des betroffenen Layouts im Layout-Designer würde die verwaiste Referenz zeigen.
- Layout-Designer unterstützt keine Mehrspalten-Zeilen über Drag & Drop, nur die oben beschriebenen eindeutigen Zeilen-/Feldoperationen (bewusste Design-Entscheidung ohne Browser-Verifikation, s. o.).
- Icon-Auswahl ist ein kuratiertes, im Frontend fest verdrahtetes Set von sieben Icons (kein Upload) — Konzept 13s offener Punkt zu Format/Herkunft der Icons ist damit für diese Session pragmatisch, aber nicht endgültig beantwortet; ein Wechsel auf ein größeres oder anpassbares Icon-Set bliebe rückwärtskompatibel, da das Backend nur einen freien String speichert.
- Keine Gruppen-Verwaltung, nur einzelne Nutzer (Permission Service unterstützt `principal_type=group` bereits, UI bietet nur `user` an).
- Workflow-Designer, Lizenzübersicht, Audit-Trail-Ansicht, Konfigurationsim-/export (Konzept 8 nennt sie für die Admin-UI) sind nicht Teil dieses Grundgerüsts — die zugrundeliegenden Services existieren noch nicht.
- i18n nur strukturell vorbereitet (ADR 0007), keine zweite Sprache und keine UI-Sprachumschaltung.
- Installationsliste ist rein lokal im Browser gespeichert, kein geräteübergreifendes Provisioning (siehe ADR 0008 "Konsequenzen") — das wäre Aufgabe des optionalen, noch nicht gebauten Fleet-/Lizenz-Management-Service (Konzept 3a, Phase 13).
- Theme-Präferenz hat keinen Konfliktauflösungsmechanismus zwischen Geräten/Installationen (letzter Fetch gewinnt) und kein Retry bei fehlgeschlagenem `PUT /me/preferences` (siehe ADR 0009 "Konsequenzen").
- `ocrEnabled` ist auf der neuen OCR-Einstellungsseite nicht editierbar, nur indirekt sichtbar (erreichbar/nicht erreichbar) — echtes An-/Ausschalten bleibt eine Deployment-Aktion (Compose-Profil), siehe ADR 0016.
- Das Ziel-Set des Storage Service (welche Backends/Zugangsdaten konfiguriert sind) ist auf der Speicher-Wächter-Seite ebenfalls nicht editierbar, nur der Admin-Override — Ziel-Set-Änderungen bleiben Deployment-Konfiguration (`DMS_TARGETS`), siehe ADR 0017.
