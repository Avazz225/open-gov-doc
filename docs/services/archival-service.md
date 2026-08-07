# archival-service

**Verantwortung:** Aussonderung & Langzeitarchivierung von Dokumenten und Umlaufmappen (5.6). **Dokumente**: überführt Dokumente nach Ablauf der Aktivphase (Objekttyp-Frist oder manueller Trigger) verpflichtend als PDF/A (Fallback: reines PDF) in ein separates Archivziel, entfernt nach einer Übergangsfrist die Live-Speicherkopie ("Dehydrieren"), und stellt einen auditierten, rollengegateten Rückhol-Vorgang bereit. **Umlaufmappen** (seit P7-S3b): erzeugt für geschlossene Cases eine echte, schemavalidierte XDOMEA-4.0.0-Aussonderungsnachricht + packt die referenzierten Dokumentinhalte in ein Übergabe-Paket. Koordiniert in beiden Fällen nur die Transfer-Mechanik — `document-service`/`case-service` bleiben alleinige Autorität für die jeweiligen Lebenszyklusfelder.

**Konzept-Referenz:** 5.6
**Eigenes Postgres-Schema:** `archival` (Tabellen `archival_transfer`, `case_archival_transfer`)

## Architekturentscheidungen

- **Kein neuer BPMN-Prozess**: `workflow-service` ist ein echter BPMN-Engine (SpiffWorkflow) ohne programmatische "Schritt-für-Schritt"-API — ein neuer Prozess bräuchte ein von Hand modelliertes Diagramm, für einen rein systemgesteuerten, linearen Ablauf ohne menschliche Zwischenschritte kein sinnvoller Mehrwert. Stattdessen **Poll-Loop + Status-Feld-Zustandsmaschine**, exakt dasselbe Idiom wie `document-service`s `_retention_poll_loop`/`reporting-service`s `_report_schedule_poll_loop`: jede Phase wird einzeln committet, bevor der nächste Schritt beginnt, ein Fehler bei einem Transfer bricht die Verarbeitung der übrigen Transfers desselben Ticks nicht ab, Wiederaufnahme nach einem Absturz ergibt sich aus dem persistierten `status`, nicht aus Engine-Checkpointing.
- **Universelle PDF/A-Konvertierung statt Original-Format-Fallback**: eine ursprünglich geplante "PDF/A wo möglich, sonst Original-Format archivieren"-Lösung wurde verworfen (Nutzervorgabe: alle gängigen Dokumenttypen müssen überführt werden können, PDF-Fallback ist okay, ein stiller Original-Format-Fallback nicht). Die eigentliche Konvertierung passiert bereits vorgelagert in `rendering-service`s `PdfArchiveRenderer` (LibreOffice headless + Pillow, seit P7-S3 — siehe `docs/services/rendering-service.md`); dieser Service liest nur die fertige `pdf_archive`-Rendition.
- **Speicherung über eine neue Archiv-Zielrolle in `storage-service`** statt eines eigenen Speichersystems: `BackendTargetConfig.role: "archive"` (siehe `docs/services/storage-service.md`) — wiederverwendet ~90% der bestehenden Multi-Backend-/Fixity-Infrastruktur (ADR 0017), ein Archiv-Ziel ist einfach ein weiteres konfiguriertes Backend, nur mit anderer Rolle (z. B. güstigerer/anders redundanter Provider).
- **"Dehydrieren" statt physischer Löschung**: die `Document`-Zeile in `document-service` wird nie gelöscht (Metadaten bleiben auffindbar, wörtliche Konzeptvorgabe) — nur der Inhalt auf den Live-Speicherzielen wird nach `Settings.dehydration_delay_days` (Default 30) entfernt, gleiches Prinzip wie `TrashConfig.restore_period_days`.
- **Legal Hold gated nur das Dehydrieren, nicht das Archivieren** — eine zusätzliche, sichere Archivkopie schadet nicht, das Entfernen der Live-Kopie dagegen schon (konsistent mit "Legal Hold überschreibt jede fällige Aktion", 5.2).
- **Verschlüsselung über ein schlankes `KeyStore`-Plugin-Interface** ([ADR 0029](../adr/0029-aussonderung-xdomea-eigenimplementierung-kdbx-plugin.md)): nur die Schnittstelle (`get_key(key_id) -> bytes`) plus eine triviale `EnvKeyStore`-Standardimplementierung (ein Schlüssel aus `DMS_ARCHIVE_ENCRYPTION_KEY`, explizit Dev-/Testzweck) werden mitgeliefert — eine echte KDBX-Anbindung (`pykeepass`, GPL-3.0) ist laut [ADR 0029](../adr/0029-aussonderung-xdomea-eigenimplementierung-kdbx-plugin.md) bewusst ein separat nachinstallierbares Paket außerhalb des Standard-Images. AES-256-GCM über `cryptography` (bereits echte Dependency in `workflow-service`/`signature-service`).
- **Rückholung als eigener, rollengegateter Vorgang**: `POST /archival-transfers/{id}/retrieve` erfordert `Settings.archive_retrieval_role` (Default `dms-admin`) im `X-DMS-Roles`-Header (vom Gateway injiziert) — gleiches Muster wie `storage_service.governance_bypass_role`. Schreibt den entschlüsselten Inhalt exakt unter denselben Live-Storage-Schlüssel zurück, den `document-service` bereits kennt (`DocumentVersionOut.storage_object_key`, seit P7-S3 öffentlich sichtbar), damit der reguläre Download-Pfad danach unverändert funktioniert.
- **Kein NATS-Consumer/-Producer**: dieser Service ist rein Poll-/HTTP-basiert (Kandidaten-Ermittlung über `GET /documents/due-for-archival`, keine Event-getriebene Auslösung) — `document.archived`/`document.dehydrated`/`document.rehydrated` werden von `document-service` selbst publiziert (Domain-Owner-Prinzip), wenn dieser Service dessen interne Rückruf-Endpunkte aufruft, nicht von diesem Service.
- **XDOMEA 4.0.0 statt 3.0.0** (seit P7-S3b, [ADR-0029-Addendum](../adr/0029-aussonderung-xdomea-eigenimplementierung-kdbx-plugin.md)): die ursprünglich in ADR 0029 benannte Version 3.0.0 lief zum Zeitpunkt der P7-S3b-Umsetzung laut offizieller KoSIT-Registrierung in Kürze aus und war nur noch über einen GPL-3.0-Drittanbieter-Mirror auffindbar — 4.0.0 ist der aktuelle Standard, sauber über die offizielle KoSIT-Schemainfrastruktur (`schema.kdo.de`, `xoev.de`) beziehbar, keine Lizenzfrage.
- **Nur die 0503-Nachricht ("Aussonderung"), nicht der volle bilaterale Verhandlungsfluss** (0501 Anbieteverzeichnis → 0502 Bewertungsverzeichnis → 0504–0507 Bestätigungen): der volle Fluss setzt ein tatsächlich antwortendes zweites System (ein Archivsystem) voraus, das es hier nicht gibt. 0503 ist die tatsächliche Export-/Übergabenachricht mit den Inhaltsdaten — ausreichend, um eine valide, an ein externes Archiv übergebbare Aussonderung zu erzeugen.

## Zustandsmaschine

`ArchivalTransfer.status`: `pending → locked → copied → verified → released → dehydrated` (+ `failed` mit `error_message`, erreichbar aus jedem aktiven Zwischenstatus).

| Status | Bedeutung | Übergang ausgelöst durch |
|---|---|---|
| `pending` | Transfer angelegt, noch nicht bearbeitet | `discover_due_transfers` (Phase 1 jedes Ticks) — legt für jedes fällige, noch nicht in Bearbeitung befindliche Dokument (`GET /documents/due-for-archival`) genau eine Zeile an |
| `locked` | Bearbeitung begonnen | Symbolische Markierung (kein verteiltes Lock-System, nur eine Instanz dieses Service vorgesehen) — sucht die `pdf_archive`-Rendition der aktuellen Dokumentversion; solange sie noch nicht `ready` ist, bleibt der Transfer hier stehen (kein Fehler, nächster Tick versucht es erneut) |
| `copied` | Archivkopie geschrieben | Rendition heruntergeladen, optional verschlüsselt (`ObjectType.archive_encryption_enabled`), per `PUT /objects/{key}/archive-copy` auf die Archiv-Ziele geschrieben |
| `verified` | Fixity-Check bestanden | `GET /objects/{key}/archive-copy/verify` — alle zurückgegebenen Kopien müssen `ok` sein, sonst `failed` |
| `released` | Dokument als archiviert markiert | `PUT /documents/{id}/archived` (document-service publiziert `document.archived`) |
| `dehydrated` | Live-Kopie entfernt | Zweite, unabhängige Tick-Phase (`run_dehydration_tick`): `released_at + dehydration_delay_days <= now`, kein aktiver Legal Hold (`GET /documents/{id}/has-active-hold`) → `DELETE /objects/{key}/live-copies` + `PUT /documents/{id}/dehydrated` |
| `failed` | Ein Schritt ist technisch fehlgeschlagen | Konvertierung fehlgeschlagen (`rendition.status == "failed"`), Verifikation nicht `ok`, oder eine unerwartete Exception — `error_message` enthält den Grund, kein automatischer Retry (bleibt terminal) |

`retrieve_archival_transfer` (Rückholung) setzt einen `released`/`dehydrated`-Transfer zurück auf `status="released"` mit neu gesetztem `released_at`/`rehydrated_at` und `dehydrated_at=null` — die Übergangsfrist bis zur nächsten Dehydrierung beginnt dadurch bewusst neu, statt sofort wieder fällig zu sein. **Achtung bei `dehydration_delay_days=0`** (z. B. zu Testzwecken): die Fälligkeitsprüfung (`released_at <= now - delay_days`) macht dann *jeden* `released`-Transfer sofort wieder fällig — eine Rückholung wird vom nächsten Tick umgehend erneut dehydriert, obwohl `released_at` gerade erst neu gesetzt wurde. Live verifiziert (siehe `PROGRESS.md` "P7-S3"); mit einer realistischen Frist (Produktions-Default 30 Tage) bleibt der wiederhergestellte Inhalt wie vorgesehen für die volle Übergangsfrist erreichbar.

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/archival-transfers?status=...` | Alle Transfers, optional nach Status gefiltert (Admin-UI-Statustabelle) |
| `GET` | `/archival-transfers/{id}` | Einzelner Transfer — `404` bei unbekannter `id` |
| `POST` | `/archival-transfers/{id}/retrieve` | Rückholung — `403` ohne `archive_retrieval_role` im `X-DMS-Roles`-Header, `404` bei unbekanntem Transfer, `409` wenn der Transfer nicht `released`/`dehydrated` ist (noch keine verlässliche Archivkopie) |
| `GET` | `/healthz` | Health-Check |

Keine `POST`-Route zum manuellen Anlegen eines Transfers — Auslösung läuft über `document-service`s `POST /documents/{id}/archive-request` (setzt `archive_after=jetzt`), der nächste Poll-Tick dieses Service holt fällige Dokumente automatisch ab.

## Datenmodell

`archival_transfer`: `id` (UUID PK), `document_id`, `status`, `archive_format` (`"pdf_a"`, nullable bis `copied`), `encrypted` (Boolean), `storage_object_key` (Archiv-Schlüssel, nullable bis `copied`), `checksum_sha256` (nullable bis `copied`), `error_message` (nullable), `locked_at`/`copied_at`/`verified_at`/`released_at`/`dehydrated_at`/`rehydrated_at` (je nullable, gesetzt beim jeweiligen Phasenübergang), `created_at`/`updated_at`.

## Anbindung an das Backend

- **document-service** (`DocumentClient`): `GET .../due-for-archival`, `GET .../{id}`, `GET .../{id}/versions/{n}`, `GET .../{id}/has-active-hold`, `PUT .../{id}/archived`, `.../dehydrated`, `.../rehydrated`. Seit P7-S3b zusätzlich `GET .../{id}/versions/{n}/content` (`download_version_content`) — liefert den tatsächlichen Dateiinhalt einer Version, für die XDOMEA-Paketierung geschlossener Umlaufmappen.
- **rendering-service** (`RenderingClient`): `GET /renditions?document_id=&version_number=` (client-seitig auf `rendition_type == "pdf_archive"` gefiltert — kein serverseitiger Filterparameter auf rendering-service-Seite), `GET /renditions/{id}/content`.
- **storage-service** (`StorageClient`): `PUT`/`GET /objects/{key}/archive-copy`, `GET .../archive-copy/verify`, `DELETE /objects/{key}/live-copies`, `PUT /objects/{key}` (Live-Ziel-Schreiben bei der Rückholung).
- **object-type-service** (`ObjectTypeClient`): `GET /object-types/{id}` — nur für `archive_encryption_enabled`.
- **case-service** (`CaseClient`, seit P7-S3b): `GET .../due-for-archival`, `GET .../{id}`, `GET .../{id}/documents` (Dokumentreferenzen inkl. fixiertem `snapshot_version_number`), `PUT .../{id}/archived`, `GET .../case-archival-config` (installationsweite Verschlüsselungs-Konfiguration).

## XDOMEA-Aussonderung für Umlaufmappen (5.6, seit P7-S3b)

Zweite Funktion dieses Service (s. o.) — erweitert die in P7-S3 gebaute Transfer-Infrastruktur um eine XDOMEA-4.0.0-Aussonderungsnachricht für geschlossene Umlaufmappen (`case-service`, 2.3), statt einer PDF/A-Kopie eines einzelnen Dokuments.

### Zustandsmaschine

`CaseArchivalTransfer.status`: `pending → locked → packaged → verified → released` (+ `failed`). **Kein `dehydrated`-Status** — anders als ein Dokument besitzt eine Umlaufmappe keinen eigenen Live-Inhalt, der entfernt werden könnte (nur Referenzen auf Dokumente mit eigenem, unabhängigem P7-S3-Archivierungs-/Dehydrierungs-Lebenszyklus). Die `Case`-Zeile selbst wird nie gelöscht.

| Status | Bedeutung | Übergang ausgelöst durch |
|---|---|---|
| `pending` | Transfer angelegt | `discover_due_case_transfers` — legt für jede fällige, geschlossene Umlaufmappe (`GET /cases/due-for-archival`) ohne bereits laufenden Transfer eine Zeile an |
| `locked` | Bearbeitung begonnen | Case + aktive (nicht weich-gelöschte) Dokumentreferenzen geladen |
| `packaged` | XDOMEA-Paket geschrieben | Pro Referenz Dokumentinhalt geladen, `xdomea.build_aussonderung_message()` erzeugt + `xdomea.validate_message()` gegen das echte vendorte Schema geprüft, alles in einer ZIP-Datei (`aussonderung.xml` + `dokumente/<paketname>` je Dokument) verpackt, optional verschlüsselt (`CaseArchivalConfig.archive_encryption_enabled`, case-service), per `PUT /objects/{key}/archive-copy` hochgeladen |
| `verified` | Fixity-Check bestanden | `GET /objects/{key}/archive-copy/verify` — alle Kopien müssen `ok` sein, sonst `failed` |
| `released` | Case als archiviert markiert | `PUT /cases/{id}/archived` (case-service publiziert `case.archived`) |
| `failed` | Ein Schritt technisch fehlgeschlagen | XDOMEA-Validierungsfehler, Verifikation nicht `ok`, oder eine unerwartete Exception — `error_message` enthält den Grund, kein automatischer Retry |

### Paket-Format

Eine ZIP-Datei (`zipfile`, Python-Standardbibliothek, keine neue Dependency): `aussonderung.xml` im Wurzelverzeichnis (die validierte 0503-Nachricht) + `dokumente/{uuid}.{ext}` je referenzierter Dokumentversion — der Dateiname innerhalb des Pakets ist **derselbe Wert**, den die XML unter `Format/Primaerdokument/Dateiname` referenziert (`xdomea.package_filename()`, deterministisch aus `document_id`/`version_number` via `uuid.uuid5`).

### XDOMEA-Nachrichtenerzeugung (`xdomea.py`)

`build_aussonderung_message(case, documents) -> bytes` baut die Nachricht `Aussonderung.Aussonderung.0503` per `lxml.etree` und bildet **`Case` → `xdomea:Vorgang`** ab (kein `Akte`-Wrapper — `Schriftgutobjekt` ist laut Schema eine `xs:choice` zwischen `Akte`/`Vorgang`, ein bloßer `Vorgang` an oberster Stelle ist strukturell gültig, passt 1:1 zu case-services flachem Datenmodell ohne Akte/Vorgang-Verschachtelung), **`CaseDocumentReference` → `xdomea:Dokument`** je aktiver Referenz mit fixiertem `snapshot_version_number`.

Bewusste Vereinfachungen (dokumentiert, nicht versteckt):
- **`Format/Name` immer Code `"100"` ("Sonstiges") + `SonstigerName`** = tatsächlicher Content-Type — keine vollständige MIME-Typ-zu-XDOMEA-Codeliste-Abbildung. Strukturell zulässig: `DateiformatCodeType` erzwingt laut Schema selbst nur `code`+optionalen `name` als freien Text plus ein `listVersionID`-Attribut, keine XSD-Enumeration der eigentlichen Codeliste (die Codeliste ist eine separate, nicht schema-erzwungene Vokabular-Referenz).
- **`Format/Version` immer `"unbekannt"`** — dieser Service verfolgt keine formatspezifische Versionsnummer (z. B. "PDF 1.4") je Dokumentversion.
- **`xdomeaUUID` deterministisch** (`uuid.uuid5`, nicht `uuid4`) aus `case_id`/`document_id` — reproduzierbar bei einem Retry desselben Transfers, nur die äußere `nachrichtenUUID` selbst ist bei jedem Aufbau neu (Schema-Vorgabe: "für jede Nachricht muss ein neuer UUID erzeugt werden").

`validate_message(xml_bytes)` validiert die erzeugte Nachricht gegen das echte, lokal vendorte Schema (`xdomea_schema/`, `lxml.etree.XMLSchema`) — wirft `ValidationError` bei jedem Schema-Verstoß, kein stiller Fallback. Ein `lxml.etree.Resolver` löst die im Schema enthaltenen externen `xoev.de`-Imports (`xoev-code.xsd`, das G2G-Basisnachrichtenmodul, DIN-91379-Datentypen) auf die lokalen Dateien auf — **kein Netzwerkzugriff zur Laufzeit oder in Tests**.

### Vendorte Schema-Dateien (`xdomea_schema/`)

7 Dateien, alle von der offiziellen KoSIT-Infrastruktur bezogen (kein GPL-Drittanbieter-Mirror, siehe `xdomea_schema/README.md` für die genauen Quell-URLs): `xdomea-Baukasten.xsd`, `xdomea-Datentypen.xsd`, `xdomea-Nachrichten-AussonderungDurchfuehren.xsd`, `xdomea-Typen-AussonderungDurchfuehren.xsd`, `xoev-code.xsd`, `xoev-basisnachricht-unqualified-g2g_1.1.xsd`, `din-norm-91379-datatypes.xsd` — exakt die Abhängigkeitskette der Nachricht `Aussonderung.Aussonderung.0503`, nicht der komplette XDOMEA-Schema-Umfang. Werden von `hatchling` automatisch als Paketdaten mitgebaut (verifiziert: `uv build --wheel` enthält alle 7 `.xsd`-Dateien im Wheel).

### API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/case-archival-transfers?status=...` | Alle Case-Transfers, optional gefiltert |
| `GET` | `/case-archival-transfers/{id}` | Einzelner Transfer — `404` bei unbekannter `id` |
| `GET` | `/case-archival-transfers/{id}/package` | Lädt das (ggf. entschlüsselte) ZIP-Paket direkt herunter — `403` ohne `archive_retrieval_role`, `404`/`409` analog zur Dokument-Rückholung. **Kein** Zurückschreiben auf ein Live-Ziel (anders als bei Dokumenten): eine Umlaufmappe besitzt keinen eigenen Live-Speicherplatz, nur ein reiner Download |

### Datenmodell

`case_archival_transfer`: `id` (UUID PK), `case_id`, `status`, `encrypted` (Boolean), `storage_object_key` (nullable bis `packaged`), `checksum_sha256` (nullable bis `packaged`), `error_message` (nullable), `locked_at`/`packaged_at`/`verified_at`/`released_at` (je nullable), `created_at`/`updated_at`.

## KeyStore-Plugin (5.6, [ADR 0029](../adr/0029-aussonderung-xdomea-eigenimplementierung-kdbx-plugin.md))

`keystore.KeyStore` (ABC, eine Methode `get_key(key_id) -> bytes`) — gleiches Plugin-Muster wie `storage_service.backends.interface.StorageBackend`. Mitgeliefert: `EnvKeyStore`, liest genau einen Schlüssel aus `Settings.archive_encryption_key` (base64, 32 Byte). **Kein Fallback auf einen zufällig erzeugten Schlüssel** bei fehlender Konfiguration — der würde bei jedem Neustart wechseln und bereits verschlüsselte Archivkopien dauerhaft unentschlüsselbar machen; `get_key()` wirft stattdessen `KeyNotFoundError`. `crypto.py` implementiert AES-256-GCM (Nonce den Ciphertext-Bytes vorangestellt) — einfacher als die RSA-hybride, installationsübergreifende Verschlüsselung in `workflow_service.federation_crypto`, da hier nur ein einziger symmetrischer Schlüssel aus dem `KeyStore` benötigt wird, keine Public-Key-Kryptographie zwischen zwei Parteien.

## Events

Keine eigenen — `document.archived`/`document.dehydrated`/`document.rehydrated` werden von `document-service` publiziert, wenn dieser Service dessen `PUT .../archived`/`.../dehydrated`/`.../rehydrated`-Endpunkte aufruft (siehe `docs/services/document-service.md`). Seit P7-S3b analog: `case.archived` wird von `case-service` publiziert, wenn dieser Service dessen `PUT .../archived`-Endpunkt aufruft (siehe `docs/services/case-service.md`).

## Selbst-Registrierung (Konzept 3.2a)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`), identisches Muster wie jeder andere Service. Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Tests

- `uv run pytest services/archival-service/tests` (**50 Tests**, davon 19 neu seit P7-S3b): `test_keystore.py`/`test_crypto.py` (Roundtrip, falscher Schlüssel, fehlender Schlüssel, frischer Nonce je Aufruf), `test_repository.py` (CRUD, aktive-Transfer-Erkennung inkl. Ausschluss terminaler Status, Dehydrierungs-Fälligkeitsfilter), `test_pipeline.py` (volle Phasenkaskade `pending → released` gegen Fake-Clients, Verharren in `locked` solange die Rendition nicht bereit ist, `failed` bei fehlgeschlagener Konvertierung/Verifikation, Verschlüsselungspfad, Dehydrierungs-Tick inkl. Legal-Hold-Blockade), `test_api.py` (Endpunkt-Verdrahtung mit gemockten externen Clients — Rollen-Gate `403`, Status-Gate `409`, erfolgreiche Rückholung inkl. Live-Upload/`mark_rehydrated`-Aufruf). Seit P7-S3b zusätzlich: **`test_xdomea.py` validiert die erzeugte Nachricht gegen das echte, vendorte XDOMEA-4.0.0-Schema** (kein Mock, keine vereinfachte Teilmenge — der wertvollste Test dieser Session, verifiziert die komplette Schema-Kette end-to-end ohne Netzwerkzugriff), `test_case_pipeline.py` (Phasenkaskade `pending → released` inkl. ZIP-Inhaltsprüfung, Ausschluss weich-gelöschter Dokumentreferenzen, Verschlüsselungspfad, `failed` bei Verifikationsfehler), `test_api.py`-Erweiterung für `/case-archival-transfers`.
- Kein eigener Live-Docker-Smoke-Test-Abschnitt hier — siehe `PROGRESS.md` "P7-S3"/"P7-S3b" für den vollständigen End-to-End-Ablauf über mehrere Services hinweg (Objekttyp mit `default_archive_after_days`, PDF/`.docx`/`.png`-Dokumente, Dehydrierung, Legal-Hold-Blockade, verschlüsselte Rückholung; seit P7-S3b zusätzlich eine geschlossene Umlaufmappe mit mehreren Dokumenten, Paket-Download, unabhängige Zweitvalidierung der `aussonderung.xml` außerhalb der Pytest-Suite).

## Offene Punkte

- **Kein Rollen-/Berechtigungscheck außer bei der Rückholung** — `GET /archival-transfers`/`.../{id}` sind wie die meisten administrativen Endpunkte dieses Systems ungegated (siehe `PROGRESS.md` "Autorisierung"), nur über die Admin-UI-Navigation praktisch auf Admins beschränkt. Gilt gleichermaßen für die neuen `/case-archival-transfers`-Endpunkte.
- **Kein Retry für `failed`-Transfers** — ein fehlgeschlagener Transfer bleibt terminal, ein neuer Anlauf bräuchte einen manuellen `POST /documents/{id}/archive-request` in `document-service` (bzw. `.../cases/{id}/archive-request` in case-service), das aber erneut denselben Aktive-Transfer-Ausschluss greifen lassen würde, solange die alte `failed`-Zeile nicht separat behandelt wird — kein Admin-UI-Bedienelement dafür in dieser Session.
- **Verschlüsselung nur mit einem einzigen, statischen Schlüssel** (`EnvKeyStore`) — kein Schlüssel-Rotation-/Multi-Tenant-Support, siehe "KeyStore-Plugin" oben.
- **Nur die 0503-Nachricht, kein voller XDOMEA-Verhandlungsfluss** (s. o.) — 0501/0502/0504–0507 sind nicht implementiert, da es kein antwortendes zweites System gibt.
- **`Format/Name` immer Code "100" statt einer vollständigen MIME-Typ-zu-XDOMEA-Codeliste-Abbildung** (s. o.) — strukturell schema-valide, aber semantisch weniger präzise als eine echte Formatzuordnung (z. B. der spezifische PDF-Code statt "Sonstiges").
- **Kein durchsuchbarer "Aussonderungs-Sonderbereich"** (2.5) — laut Roadmap explizit Teil von Phase 15 ("Sonderbereiche"), die Admin-UI-Tabelle (`/archival-transfers/`) ist reine Verwaltungs-/Status-Ansicht, keine Endnutzer-Suchoberfläche.
