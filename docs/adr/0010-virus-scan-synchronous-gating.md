# 0010 — Virus-Scan: synchrones Gating im Upload-Pfad statt asynchronem Scan-Status

**Status:** akzeptiert
**Kontext:** Konzept 10.3, Session P5-S1

## Entscheidung

Der Document Service ruft den neuen Virus-Scan Service **synchron auf, bevor** er Inhalt oder Metadaten eines Uploads persistiert — sowohl beim initialen Anlegen (`POST /documents`) als auch beim Check-in einer neuen Version (`POST /documents/{id}/versions`). Fällt der Scan negativ aus, wird die gesamte Anfrage mit `422` abgelehnt: kein Dokument/keine Version wird angelegt, nichts wird im Storage Service abgelegt. Es gibt **keinen** neuen Gating-Zustand (z. B. `scan_status: pending/clean/infected`) an `document`/`document_version`.

Ist der Virus-Scan Service nicht erreichbar, wird der Upload ebenfalls abgelehnt (`503`, fail-closed) statt stillschweigend durchgelassen zu werden.

## Begründung

- **10.3 fordert wörtlich** "Virenscan verpflichtend vor Freigabe eines Uploads". Der bestehende Upload-Pfad des Document Service (`POST /documents`/`POST /documents/{id}/versions`) legt Inhalt sofort im Storage Service ab und macht ihn über `GET .../content` unmittelbar abrufbar — ein rein asynchroner Konsum von `document.version.created` durch einen Virus-Scan-Service würde erst *nach* dieser Freigabe reagieren und das Versprechen verletzen.
- **Bestehendes Präzedens im selben Service**: Der Document Service validiert bereits heute synchron gegen den Object-Type Service (`object_type_client.validate(...)`, vor dem eigentlichen Schreiben), bevor ein Upload angenommen wird. Der Virus-Scan folgt demselben, bereits etablierten Muster, statt eine zweite, andersartige Integrationsart (asynchrones Event-Gating) einzuführen.
- **Kein neuer Gating-Zustand nötig**: Eine `scan_status`-Spalte hätte mehrere Stellen betroffen (jeder Lesezugriff auf Inhalt, Rendering/OCR in P5-S2/S3 müssten den Zustand zusätzlich prüfen) und wäre eine invasivere, fehleranfälligere Änderung gewesen als ein einzelner Aufruf vor dem Schreiben. Da der synchrone Scan verhindert, dass ein infiziertes Dokument überhaupt erst entsteht, muss kein nachgelagerter Service je einen Zwischenzustand kennen.
- **Fail-closed bei Nichterreichbarkeit**: Die Alternative (Upload bei Scan-Fehler durchlassen) widerspräche "verpflichtend" - ein Ausfall des Virus-Scan Service darf keine Sicherheitslücke öffnen.
- **Quarantäne statt Löschen**: Bei einem Fund wird die Datei nicht verworfen, sondern über den Storage Service unter `quarantine/{scan_id}` abgelegt (Nachvollziehbarkeit/Beweiswert, 10.3 selbst macht dazu keine Vorgabe) - der Virus-Scan Service hält wie der Document Service selbst keine Bytes, sondern delegiert an den Storage Service.
- **Engine austauschbar, aber nicht ClamAV als Standard**: Nach demselben Plugin-Prinzip wie die Storage-Backends (3.3/3.8) ist die Scan-Engine ein austauschbares Interface (`ScanEngine`). Standard ist eine `EicarSignatureEngine` (erkennt nur die genormte EICAR-Testsignatur), nicht die naheliegende `ClamdEngine` gegen einen `clamd`-Daemon: `clamd` lädt beim ersten Start seine Signaturdatenbank über `freshclam` nach, was in dieser Entwicklungsumgebung Minuten dauert und verlässlichen Internetzugriff auf die ClamAV-Mirrors voraussetzt - für einen reproduzierbaren `docker compose up`/Testlauf nicht geeignet. `ClamdEngine` ist vollständig implementiert (INSTREAM-Protokoll) und über `DMS_SCAN_ENGINE=clamd` aktivierbar, sobald ein `clamd` separat betrieben wird.

## Konsequenzen

- Upload-Latenz enthält jetzt die Scan-Zeit (bei der `EicarSignatureEngine` vernachlässigbar, bei einer echten Engine wie `clamd` je nach Dateigröße spürbar) - für dieses Grundgerüst akzeptiert, bei Bedarf später durch Chunked-Scanning/Streaming zu optimieren.
- Der Check-in-Pfad scannt auch dann, wenn `expected_base_version_number` bereits veraltet ist oder ein Lock-Konflikt vorliegt (Scan läuft vor der eigentlichen Konflikterkennung) - unnötige, aber nicht falsche Arbeit; keine Korrektheitslücke.
- Kein eigener Freigabe-/Lösch-Workflow für Quarantäne-Objekte (Wiederherstellen, endgültiges Löschen) - liegt außerhalb des Scopes dieser Session.
- Die "Benachrichtigung des Uploaders" bei einem Fund (10.3 erwähnt sie nicht explizit, aber naheliegend) ist noch nicht umgesetzt, da der Notification Service erst in P6-S2 entsteht - `virus_scan.completed` wird bereits publiziert und kann dort ohne Änderung am Virus-Scan Service konsumiert werden.
- OCR (P5-S3) und Rendering/Preview (P5-S2) docken an `document.version.created` an, das erst *nach* einem sauberen Scan publiziert wird - beide Sessions müssen sich um das Scan-Gating selbst nicht kümmern.
