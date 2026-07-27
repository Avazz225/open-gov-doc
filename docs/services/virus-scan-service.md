# virus-scan-service

**Verantwortung:** Verpflichtender Virenscan vor Freigabe eines Uploads (Konzept 10.3), Quarantäne infizierter Dateien.
**Konzept-Referenz:** 10.3
**Eigenes Postgres-Schema:** `virus_scan` (`scan_result`).

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/scan` | Multipart (`file`, optional `document_id`/`created_by`) → führt den Scan durch, legt bei Fund eine Quarantänekopie im Storage Service ab, persistiert und liefert das Ergebnis (`ScanResultOut`) |
| `GET` | `/scans/{id}` | Einzelnes Scan-Ergebnis — 404 bei unbekannter `id` |
| `GET` | `/scans?document_id=...` | Alle Scans zu einem Dokument (neueste zuerst) |
| `GET` | `/healthz` | Health-Check |

`document_id` ist beim initialen Upload noch unbekannt (der Scan läuft *vor* der Dokumenterstellung, siehe unten) — dort wird `null` übergeben; beim Check-in einer Version ist die `document_id` bereits bekannt.

## Synchrones Gating statt Scan-Status (ADR 0010)

Der Document Service ruft `/scan` **synchron auf, bevor** er Inhalt/Metadaten eines Uploads persistiert (`POST /documents`, `POST /documents/{id}/versions`) — nicht als asynchroner Konsument von `document.version.created`. Grund: 10.3 verlangt Virenscan "verpflichtend vor Freigabe", der bestehende Upload-Pfad macht Inhalte aber sofort abrufbar, sobald sie geschrieben sind. Ein rein event-getriebener Scan würde erst nach der Freigabe reagieren. Fällt der Scan negativ aus, lehnt der Document Service die gesamte Anfrage mit `422` ab — es entsteht kein Dokument/keine Version, nichts wird im Storage Service abgelegt. Ist der Virus-Scan Service nicht erreichbar, wird der Upload ebenfalls abgelehnt (`503`, fail-closed). Details/Begründung: [ADR 0010](../adr/0010-virus-scan-synchronous-gating.md).

## Engine-Plugins (3.3/3.8)

Austauschbar über `DMS_SCAN_ENGINE` (Interface: `virus_scan_service.engines.ScanEngine`):

| Wert | Engine | Hinweis |
|---|---|---|
| `eicar` (Standard) | `EicarSignatureEngine` | Erkennt ausschließlich die genormte EICAR-Testsignatur (branchenüblich für Integrationstests) — kein echter Malware-Schutz. |
| `clamd` | `ClamdEngine` | Spricht einen separat betriebenen `clamd`-Daemon über dessen INSTREAM-Protokoll an (`DMS_CLAMD_HOST`/`DMS_CLAMD_PORT`). Nicht Standard in dieser Entwicklungsumgebung, da der initiale Signaturdatenbank-Download (`freshclam`) Minuten dauert und Internetzugriff auf die ClamAV-Mirrors voraussetzt. |

Bei Fund wird die Datei über den Storage Service unter dem Key `quarantine/{scan_id}` abgelegt (Quarantäne statt Löschen, Nachvollziehbarkeit/Beweiswert) — der Virus-Scan Service hält wie der Document Service nie selbst Dateiinhalte.

## Anbindung an das Backend

- **Storage Service** (3.6): `PUT /objects/quarantine/{scan_id}` bei einem Fund.
- Kein Aufruf anderer Services für den Scan selbst — die Engine läuft in-process.

## Events

| Event | Payload |
|---|---|
| `virus_scan.completed` | `{document_id, filename, status: "clean"\|"infected", threat_name, created_by}` |

Wird für **jeden** Scan publiziert, nicht nur bei einem Fund — der Audit Service konsumiert `virus_scan.>` (seit dieser Session, siehe `docs/services/audit-service.md`) und protokolliert damit lückenlos, was gescannt wurde (5.3 verlangt dies explizit für OCR-artige Verarbeitungsschritte, hier analog angewandt).

## Selbst-Registrierung (Konzept 3.2a)

Meldet sich beim Start über `dms-registry-client` selbst bei der Registry an — Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Tests

- `uv run pytest services/virus-scan-service/tests` (17 Tests): Engine-Verhalten (EICAR-Erkennung inkl. eingebetteter Signatur, Factory-Auswahl, `ClamdEngine` wirft bei nicht erreichbarem Daemon statt fälschlich "clean" zu melden), Repository (CRUD, Filter nach `document_id`), API (`/scan` clean/infiziert inkl. Quarantäne-Key, `/scans`-Endpunkte) — läuft gegen echtes Postgres/den echten Storage Service, keine Mocks (gleiche Begründung wie bei den übrigen Backend-Services).
- Document-Service-Tests decken die Integration ab (`test_create_document_rejects_infected_upload`, `test_checkin_rejects_infected_version_without_creating_it`) — Upload mit EICAR-Inhalt wird mit `422` abgelehnt, es entsteht keine (weitere) Version.

## Offene Punkte

- **`ClamdEngine` nicht produktiv verdrahtet**: Code existiert und ist über `DMS_SCAN_ENGINE=clamd` aktivierbar, aber kein `clamd`-Container ist Teil von `infra/docker-compose.yml` (Begründung: siehe oben/ADR 0010). Nachzuholen, sobald eine Umgebung mit verlässlichem Zugriff auf die ClamAV-Signaturdatenbank verfügbar ist.
- **Kein Freigabe-/Lösch-Workflow für Quarantäne-Objekte**: Wiederherstellen oder endgültiges Löschen einer Quarantänekopie ist nicht Teil dieses Grundgerüsts.
- **Keine Benachrichtigung des Uploaders bei einem Fund**: Der Notification Service existiert erst ab P6-S2; `virus_scan.completed` wird bereits publiziert und kann dort ohne Änderung an diesem Service konsumiert werden.
- **Keine Autorisierung** (wie bei allen bisherigen Services): Gateway prüft nur Token-Gültigkeit, keine Rollenprüfung.
- **Scan-Latenz erhöht Upload-Latenz** (ADR 0010) — bei der `EicarSignatureEngine` vernachlässigbar, bei `clamd`/großen Dateien potenziell spürbar.
