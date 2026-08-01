# signature-service

**Verantwortung:** Signature Service (Konzept 3.10) - eIDAS-konforme elektronische Signatur (SES/AES/QES), Broker vor externen QTSPs über austauschbare Signature-Provider-Connectoren (Plugin-Prinzip wie Storage-Backends/CMIS, 3.3). Diese Session (P6-S7) implementiert das Grundgerüst + einen real funktionierenden internen, selbstsignierten Connector für SES/AES sowie den neuen "Signature Task"-Typ im Workflow Service (7.1) — QES über einen echten akkreditierten QTSP ist bewusst nicht Teil dieser Session (siehe [ADR 0025](../adr/0025-signature-service-internal-ca-and-connector-plugin.md)).

**Konzept-Referenz:** 3.10, 2.1a, 7.1
**Eigenes Postgres-Schema:** `signature` (Tabellen `signature`, `internal_ca`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/signatures` | Signiert eine Dokumentversion (`document_id`, `level`: `ses`\|`aes`\|`qes`, `signer_principal_id`, optional `version_number`/`reason`) - `400` bei Nicht-PDF-`content_type`, unbekanntem Principal, zu niedrigem Niveau gegenüber dem Objekttyp-Minimum, oder fehlendem Connector fürs angeforderte Niveau; `404` bei unbekanntem Dokument/unbekannter Version. Erzeugt bei Erfolg eine **neue Dokumentversion** bei document-service (s. u.) |
| `GET` | `/signatures?document_id=...` | Signaturen eines Dokuments |
| `GET` | `/signatures/{id}` | Einzelne Signatur - `404` |
| `GET` | `/signatures/{id}/verify` | Verifiziert die Signatur erneut gegen die aktuell bei document-service hinterlegten Bytes (`valid`, `integrity_intact`, `certificate_expired`, `errors[]`) |
| `GET` | `/healthz` | Health-Check |

## Datenmodell

- `internal_ca`: Singleton (`id=1`) - `certificate_pem`, `private_key_pem`, `created_at`. Selbstsignierte interne Root-CA (RSA 2048, 20 Jahre Gültigkeit), beim ersten Start generiert (`connectors/internal.generate_root_ca`), danach idempotent wiederverwendet - ein Neustart darf keine neue CA erzeugen, sonst wären zuvor ausgestellte Signaturen nicht mehr verifizierbar.
- `signature`: `document_id`, `source_version_number` (die signierte Ausgangsversion), `version_number` (die neu entstandene, signierte Version), `level`, `connector_id`, `signer_principal_id`, `signer_display_name`, `certificate_subject`/`certificate_serial`/`certificate_not_before`/`certificate_not_after`, `reason`, `signed_at`.

## Signature-Provider-Connectoren (3.10, Plugin-Prinzip wie 3.3)

`SignatureProviderConnector`(ABC, `connectors/interface.py`): `sign(pdf_bytes, signer, level)`/`verify(pdf_bytes)`. Factory (`connectors/__init__.py`) dispatcht auf `type` bei stabiler `id`-Zuordnung (wie `storage_service.backends.build_backend`, ADR 0017), konfiguriert über `DMS_SIGNATURE_PROVIDERS` (JSON-Liste). Default-Seed: `{id: "internal", type: "internal", levels: ["ses","aes"]}`.

- **`InternalSelfSignedConnector`** (`connectors/internal.py`, einzig real implementiert): stellt je Signaturvorgang ein von der internen Root-CA signiertes Leaf-Zertifikat aus - `level="ses"` mit generischem Subject (`CN=DMS System (SES)`), `level="aes"` mit personenbezogenem Subject (`CN=<Anzeigename>`, `emailAddress=<E-Mail>`, aus einer echten `auth-service`-Kontenprüfung). Bettet das Zertifikat per **pyHanko** (`SimpleSigner.load()` + `async_sign_pdf()`, PAdES-B-B) in die PDF-Bytes ein. `verify()` nutzt `async_validate_pdf_signature()` mit einer `ValidationContext`, deren einziger Trust Root die interne CA ist (`allow_fetching=False`, `revocation_mode="soft-fail"` - keine echte OCSP/CRL-Infrastruktur vorhanden).
- **`type: "qtsp"`** ist im Konfigurationsschema vorgesehen, aber **nicht implementiert** - ein Konfigurationsversuch schlägt in der Factory mit einer klaren Fehlermeldung fehl. Kein akkreditierter externer Vertrauensdiensteanbieter verfügbar/testbar in dieser Session (siehe "Offene Punkte").

## Signieren erzeugt eine neue Dokumentversion (2.1a)

Eine PAdES-Signatur verändert zwangsläufig die PDF-Bytes (das ist der springende Punkt der kryptografischen Bindung). `POST /signatures` lädt die zu signierende Version über `document_client.py` von document-service, signiert, und checkt die signierten Bytes als **neue Version** ein (`POST /documents/{id}/versions`, `expected_base_version_number = die signierte Ausgangsversion`) - die unsignierte Ursprungsversion bleibt unangetastet abrufbar. Wird nicht die aktuelle Hauptversion signiert, entsteht dank document-services bestehender optimistischer Konflikterkennung (4.2) automatisch eine Konfliktkopie statt die Hauptversion zu verschieben - keine Sonderbehandlung hier nötig. Der resultierende `Signature`-Datensatz verweist auf `source_version_number` (Eingabe) und `version_number` (das tatsächliche Ergebnis, Haupt- oder Konfliktversion).

## Mindest-Signaturniveau je Objekttyp (3.10)

`object-type-service` bekam eine additive Spalte `required_signature_level` (`ses`/`aes`/`qes`/`NULL`, nur für `applies_to="document"`, siehe `docs/services/object-type-service.md`). `POST /signatures` fragt sie über `object_type_client.py` ab (nur falls das Dokument einen Objekttyp hat) und lehnt ein zu niedriges angefordertes Niveau mit `400` ab.

## Signer-Existenzprüfung (Retrofit-Muster aus P6-S6)

`signer_principal_id` bleibt ein selbstberichtetes Body-Feld (konsistent mit `triggered_by`/`approved_by`/`completed_by`/`lifted_by` im gesamten Projekt), wird aber gegen ein echtes `auth-service`-Konto geprüft (`auth_client.py`, `GET /users`, Anmeldung als technisches `users-admin`-Konto - `GET /users` ist seit P6-S5 gegated) und liefert Anzeigename/E-Mail fürs AES-Zertifikat. `400` bei unbekanntem Principal.

## Signature Task im Workflow Service (7.1, seit P6-S7)

`workflow-service`s `spiff_adapter.py` wechselt den BPMN-Parser auf `SpiffWorkflow.camunda.parser.CamundaParser` (mappt `manualTask` weiterhin auf `ManualTask`, füllt aber zusätzlich `task_spec.extensions` aus `bpmn:extensionElements/camunda:properties`). Ein `<bpmn:manualTask>` mit `camunda:properties` `taskType=signature`/`requiredLevel=...` wird dadurch als Signature Task erkennbar, bleibt aber technisch ein gewöhnlicher Manual Task - kein neues BPMN-Element, kein Modeler-Tooling-Bruch, kein Prozess-Designer-Palette-Eintrag in dieser Session (folgt mit P6-S8). Die zu signierende `document_id` läuft über die bestehende generische `data`-Prozessvariable.

`GET /instances/{id}/tasks` surfacet `extensions` je Task. `POST .../tasks/{id}/complete` verlangt bei einer Signature Task ein `signature_id`-Feld; ein neuer, dünner `signature_client.py` (Muster wie `permission_client.py` aus P6-S6) prüft bei diesem Service (`GET /signatures/{id}`), dass die Signatur existiert, zum in den Task-Daten hinterlegten `document_id` passt und mindestens das verlangte Niveau hat - sonst `400`. Details/Beispiel-Fixture: `docs/services/workflow-service.md`.

## Not-Shutdown-Interaktion (4.8)

`POST /signatures` steht **nicht** auf der Gateway-Allow-Liste - während des Wartungsmodus blockt das Gateway diesen (wie jeden anderen nicht gelisteten) Endpunkt automatisch mit `503` (Default-Deny, siehe [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md)). Keine Sonderbehandlung nötig, da Signieren eine schreibende, nicht zeitkritische Admin-/Fachaktion ist.

## Events

**Publiziert** (Stream `signature`, `ensure_stream=True`):

| event_type | payload |
|---|---|
| `signature.created` | `{version_number, level, signer_principal_id, connector_id}` |

Kein Konsument - reiner Producer, wie `workflow-service`.

## Selbst-Registrierung (Konzept 3.2a)

Registriert sich beim Start selbst bei der Registry, identisches Muster wie jeder andere Service.

## Sensoren (Konzept 10.1)

Noch keine - folgt in Phase 11.

## Tests

`uv run pytest services/signature-service/tests` - läuft gegen eine echte Postgres-Instanz und echte Aufrufe an document-service/object-type-service/auth-service, kein Mocking von Sibling-Services:

- Signieren SES/AES mit einer echten, per `pypdf` erzeugten Test-PDF-Fixture, echte pyHanko-Signatur, echte neue Dokumentversion bei document-service, echte Verifikation (`valid: true`).
- Objekttyp-Mindestniveau-Gate (`400` bei zu niedrigem Niveau, `201` bei ausreichendem).
- Ablehnung bei Nicht-PDF-Dokument, unbekanntem Dokument, unbekanntem Signer-Principal, `level="qes"` ohne konfigurierten Connector.
- Liste/Detail/Verify inkl. `404`-Fälle.
- **11 Tests.**
- Reine Backend-Session, kein Browser-Test nötig (User-UI-Anbindung siehe `docs/services/user-ui.md`).

## Offene Punkte

- **QES vollständig unimplementiert** - weder ein echter QTSP-Connector noch ein Testfall dafür existieren; ein Signaturversuch mit `level="qes"` schlägt unabhängig vom Objekttyp mit `400` fehl ("kein Connector konfiguriert"). Erfordert eine externe Geschäftsbeziehung mit einem akkreditierten Vertrauensdiensteanbieter, siehe [ADR 0025](../adr/0025-signature-service-internal-ca-and-connector-plugin.md).
- **Kein PAdES-B-LTA/Langzeitarchivierung** - nur PAdES-B-B umgesetzt (kein Timestamp-Authority-Countersigning). 3.10 nennt B-LTA explizit für die Aussonderung (5.6) - ein künftiges Nachrüsten bräuchte eine echte Timestamp Authority.
- **Keine OCSP/CRL-Sperrprüfung** - `GET /signatures/{id}/verify` prüft nur Integrität und Zertifikats-Gültigkeitszeitraum. Für eine selbstsignierte interne CA ohne echte Sperrlisten-Infrastruktur die einzig ehrliche Verifikationstiefe.
- **Kein Prozess-Designer-Palette-Eintrag für Signature Tasks** - BPMN-Modellierung bleibt roher XML-Upload, ein Signature Task muss die Extension-Attribute von Hand im XML setzen. Folgt mit P6-S8.
- **Kein PKCS#11/HSM-Support** - 3.10 erwähnt pyHanko explizit auch für Hardware-Token/HSM-Anbindung; diese Session nutzt ausschließlich In-Memory-generierte Software-Schlüssel.
- **Nur PDF-Dokumente signierbar** - PAdES ist PDF-spezifisch (durch pyHanko selbst vorgegeben); XAdES/CAdES für andere Formate sind nicht umgesetzt.
- **Technisches Konto `users-admin` dient auch hier als interne Service-Anmeldung** - wie bei notification-service (P6-S6) authentifiziert sich signature-service für die Signer-Existenzprüfung als fremdes technisches Konto statt einer eigenen Identität (siehe ADR 0024 "Konsequenzen" für die bereits vermerkte Revisitierungs-Empfehlung).
- **Keine Admin-UI-Konfiguration für Connectoren** - `DMS_SIGNATURE_PROVIDERS` ist reine Env-Var-Konfiguration, konsistent mit Storage-Backends (ebenfalls ohne Admin-UI-Konfiguration).
