# 0091 — storage-service/signature-service: Betriebsparameter live-editierbar, Ziel-/Connector-Liste bleibt env-var-only

**Status:** akzeptiert (Post-Roadmap Phase 22 Session 6)
**Kontext:** Post-Roadmap Phase 22 Session 6, betrifft `storage-service`, `signature-service`, `admin-ui`

## Entscheidung

`storage-service`s Ziel-Set (`Settings.targets`, inkl. S3-Zugangsdaten `access_key`/`secret_key`) und
`signature-service`s Connector-Set (`Settings.signature_providers`) waren bislang reine Pydantic-Settings
aus Env-Vars, an vielen Stellen im Code direkt gelesen, nur bei einem Neustart wirksam änderbar. Diese
Session macht einen bewusst eng geschnittenen Teil davon live-editierbar über neue `GET`/`PUT
/operational-config` (`storage-service`) bzw. `GET`/`PUT /signature-config` (`signature-service`)
Endpunkte — nach demselben Get-or-create-Singleton-Muster wie `OcrConfig`/`GuardConfig` (bei jedem
Zugriff frisch aus der DB gelesen, kein `app.state`-Cache, daher ohne Neustart wirksam):

1. **`storage-service`**: `write_strategy`, `quorum_count`, `max_replication_attempts` — reine
   Betriebsparameter ohne Geheimnisse.
2. **`signature-service`**: `levels` je bereits konfiguriertem Connector — ebenfalls ohne Geheimnisse
   (Zertifikate/Schlüssel selbst liegen in `InternalCa`, unberührt von dieser Session).
3. **Bewusst NICHT live-editierbar** (bleibt env-var-only, nur bei Neustart änderbar): die Ziel-/
   Connector-**Liste** selbst (`id`, `type`, `base_path`/`endpoint_url`/`access_key`/`secret_key`/
   `bucket`/`region` bei Storage-Zielen; `id`, `type` bei Signatur-Connectoren) sowie
   `object_lock_mode`/`role` je Storage-Ziel.
4. **Neue Admin-UI-Seiten**: `/storage-operational-config/` (Formular) und `/signature-config/`
   (Tabelle mit Level-Checkboxen je Connector).

## Begründung

- **Warum Zugangsdaten/Struktur bewusst ausgeklammert bleiben** (Nutzervorgabe für diese Session,
  per Rückfrage geklärt): `access_key`/`secret_key` live editierbar zu machen hätte eine neue
  Verschlüsselungs-/Masking-Infrastruktur erfordert (Klartext darf nie in einer `GET`-Antwort
  auftauchen) — eine deutlich größere, sicherheitskritischere Änderung als der Rest dieser Session.
  `object_lock_mode`/`role` sind WORM-/Aussonderungs-relevant (5.1/5.2a/5.6) — ein versehentlicher
  Live-Wechsel hätte compliance-relevante Konsequenzen (z. B. ein Governance-Ziel, das plötzlich kein
  Objekt-Lock mehr durchsetzt). Beide bleiben bewusst nur per Neustart änderbar, bis eine künftige
  Session das dediziert und mit eigener Sorgfalt angeht.
- **Warum "nur bestehende Einträge bearbeiten", keine CRUD-Verwaltung der Liste** (Nutzervorgabe): die
  Menge konfigurierter Ziele/Connectoren ist strukturell an tatsächlich vorhandene Infrastruktur
  gebunden (ein `id` ohne zugehörige echte Backend-Instanz wäre bedeutungslos) — Anlegen/Entfernen bleibt
  ein Deployment-Vorgang (Env-Var + Neustart), nicht ein Admin-UI-Klick. `PUT /operational-config` hat
  ohnehin keine Liste (nur Skalare); `PUT /signature-config` lehnt unbekannte Connector-`id`s mit `422`
  ab.
- **Warum Live-Reload statt "wirkt erst nach Neustart"** (Nutzervorgabe): entspricht der bereits
  etablierten Erwartungshaltung an eine Admin-UI-Einstellungsseite in diesem Projekt (`OcrConfig`,
  `GuardConfig`) — ein Admin, der einen Wert ändert, erwartet, dass er wirkt, nicht dass er sich einen
  Service-Neustart merken muss.
- **Warum `write_strategy`/`quorum_count`/`max_replication_attempts` bei jedem betroffenen Request neu
  aus der DB gelesen werden, statt in `app.state` gecacht zu bleiben**: exakt das bereits etablierte
  Muster von `GuardConfig` in genau diesem Service (`repository.get_guard_config`, jedes Mal neu
  gelesen) — ein zusätzlicher, indizierter Primärschlüssel-Read pro betroffenem Request ist der
  akzeptierte Preis für Live-Reload ohne eigene Invalidierungslogik.
- **Warum die Quorum-Erfüllbarkeits-Prüfung (`_validate_settings`) bei `PUT /operational-config`
  wiederholt wird**: die Zielanzahl ist strukturell fest (env-var, diese Session ändert daran nichts) -
  ein Admin könnte sonst live einen `quorum_count` setzen, den kein tatsächlicher Schreibvorgang mehr
  erfüllen kann, unbemerkt bis zum nächsten Upload-Fehlschlag.
- **Warum `signature-service`s Validierung (`levels` nicht leer, `type=internal` kein QES) im
  Repository dupliziert statt aus `SignatureProviderConfig._check_levels` wiederverwendet wird**: der
  Pydantic-`model_validator` ist an die Instanziierung eines `SignatureProviderConfig`-Objekts gebunden
  (Settings-Schema-Kontext), die Laufzeitprüfung braucht dieselbe Regel aber unabhängig von einer
  konkreten Pydantic-Modell-Instanz (nur `id`+`levels`+`type` als lose Werte). Beide Stellen sind kurz
  genug, dass eine gemeinsame Extraktion mehr Indirektion als Nutzen gebracht hätte.

## Konsequenzen

- **Migration**: keine (zwei brandneue Tabellen `storage.operational_config`/
  `signature.signature_config`, `Base.metadata.create_all` legt sie automatisch an).
- **Testinfrastruktur-Fund**: `signature-service`s `tests/conftest.py`-Truncate-Liste fehlte die neue
  Tabelle (behoben, gleicher Fund wie bereits in P22-S2 bei `permission-service`). `storage-service`
  hat GAR KEINE Truncate-Fixture (bestehende Tests setzen stattdessen auf pro-Test-eindeutige
  Objektschlüssel) — die neuen, den DB-Singleton `operational_config` mutierenden Tests bekamen
  deshalb eine eigene, lokale Restore-Fixture (`operational_config_client`), die die Env-Var-Defaults
  nach jedem Test wiederherstellt, statt die Testinfrastruktur des gesamten Service umzubauen. Beide
  Fixes wurden durch zweimaliges Hintereinander-Ausführen der jeweiligen Testsuite verifiziert (das
  exakte Symptom, das sonst erst bei einem zweiten, unabhängigen Testlauf aufgefallen wäre).
- **Tests**: `storage-service` 117 (vorher 113, +4: `GET`-Default, `PUT`-Persistenz, Quorum-Ablehnung,
  Ende-zu-Ende-Beweis über einen echten Upload nach `PUT`); `signature-service` 16 (vorher 11, +5:
  `GET`-Default, drei Validierungsfälle, Ende-zu-Ende-Beweis über einen fehlschlagenden AES-Sign-Versuch
  nach Entfernen von AES aus den Niveaus, gefolgt von einem erfolgreichen SES-Versuch). `admin-ui` 201
  (vorher 191, +10: `storage-operational-config.test.tsx` 4 Tests, `signature-config.test.tsx` 6 Tests).
- **Live gegen den echten laufenden Stack verifiziert** (Image-Neubau + Neustart von
  `storage-service`/`signature-service`/`admin-ui`): `GET /operational-config`/`GET /signature-config`
  zeigten die korrekten Env-Var-Ausgangswerte; ein `PUT` mit `quorum_count=2` (nur 1 reguläres Ziel im
  Dev-Stack konfiguriert, das zweite trägt `role=archive`) lieferte `422`; ein satisfiables `PUT` auf
  `strategy=quorum` gefolgt von einem echten Objekt-Upload lief erfolgreich über den Quorum-Codepfad,
  ganz ohne Neustart zwischen `PUT` und Upload; `signature-config`s `PUT` mit `levels=["qes"]` für den
  `internal`-Connector sowie mit einer unbekannten Connector-`id` lieferten beide `422`. Alle
  Testdaten/Konfigurationsänderungen anschließend zurückgesetzt. Kein interaktiver Browser-Test der
  beiden neuen Admin-UI-Seiten (kein Browser/Playwright in dieser Entwicklungsumgebung verfügbar,
  projektweit etablierte Praxis) — stattdessen über die Vitest-Komponententests sowie die obige
  Backend-API-Verifikation über exakt dieselben Gateway-Aufrufe abgesichert.
- Doku: neues [ADR 0091](0091-connector-operational-config-live-editable.md),
  `docs/services/storage-service.md`, `docs/services/signature-service.md` (jeweils API-Tabelle, neue
  Sektion, Tests-Sektion), `docs/services/admin-ui.md` (Seiten-Tabelle, neue Sektion, Backend-
  Anbindungstabelle, Tests-Sektion) ergänzt.
