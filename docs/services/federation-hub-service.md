# federation-hub-service

**Verantwortung:** Federation Hub Grundgerüst (Konzept 7.4) — Adressbuch verbundener Installationen + Schaltzentrale, die installationsübergreifende Workflow-Handover vermittelt, ohne die dabei übertragenen Inhalte selbst einsehen zu können. **Bewusst kein interner Service einer Installation** — registriert sich nicht bei der `registry-service`, hat kein `depends_on: gateway-service`, keinen Event-Bus-Producer/-Consumer. Für lokale Entwicklung/den Selbst-Loopback-Smoke-Test trotzdem in `infra/docker-compose.yml` mitgeliefert (dev-only Convenience) — ein Betreiber würde ihn in Produktion vollständig separat betreiben, ggf. sogar für mehrere fremde Installationen gemeinsam (Konzept 7.4 "Betreibermodell").

**Konzept-Referenz:** 7.4
**Eigenes Postgres-Schema:** `federation` (Tabellen `hub_identity`, `installation`, `handover`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/public-key` | Öffentlicher Signaturschlüssel des Hub (RSA-2048) — Installationen rufen dies einmalig beim ersten Registrieren ab (Trust-on-First-Use, siehe ADR 0028) |
| `POST` | `/installations` | Registrieren (neue `id`, kein Auth nötig, generiert+gibt einmalig einen Klartext-API-Key zurück) oder Aktualisieren (bekannte `id`, verlangt `Authorization: Bearer <api_key>` — sonst `401`) |
| `GET` | `/installations` | Adressbuch — ungegated, liefert nie `api_key_hash` |
| `DELETE` | `/installations/{id}` | Deregistrieren — verlangt den zur Installation gehörenden API-Key |
| `POST` | `/handovers` | Handover anlegen — **`handover_id` wird vom Aufrufer selbst mitgegeben**, nicht vom Hub generiert (siehe "Erst beim Live-Smoke-Test gefunden" unten). `Authorization: Bearer` bestimmt die Absenderinstallation, prüft Versionskompatibilität (`409` bei Inkompatibilität), committet die Handover-Zeile **vor** dem Zustellversuch, verschlüsselten Payload dann synchron an `to_installation`s `callback_base_url` + `/federation/inbound` zustellen, Ergebnis (`delivered`/`delivery_failed`) direkt zurückgeben |
| `POST` | `/handovers/{id}/result` | Ergebnis zurückmelden — nur durch die Zielinstallation des Handover (`403` sonst), leitet an `from_installation`s Callback + `/federation/inbound-result` weiter |
| `GET` | `/handovers/{id}` | Status/Metadaten eines Handover |
| `GET` | `/healthz` | Health-Check |

## Datenmodell

- `hub_identity`: Singleton (`id=1`, gleiches Muster wie `signature-service`s `InternalCa`) — eigenes RSA-2048-Signaturschlüsselpaar, mit dem jede Zustellung an eine Installation signiert wird (`X-Federation-Hub-Signature`).
- `installation`: Adressbucheintrag — `id` (von der Installation selbst gewählt), `display_name`, `callback_base_url`, `public_key_pem` (für Ende-zu-Ende-Verschlüsselung durch ANDERE Installationen, der Hub selbst besitzt nie den passenden privaten Schlüssel), `api_key_hash` (SHA-256, siehe ADR 0028 zur Begründung des schnellen statt langsamen Hash-Verfahrens), `version`/`min_compatible_peer_version`, `supported_process_types`/`supported_document_types` (JSON-Listen, aktuell nur gespeichert, keine erzwingende Prüfung).
- `handover`: **nur Metadaten** (`from_installation_id`, `to_installation_id`, `process_type`, `status`, Zeitstempel) — kein Feld für den Chiffretext selbst, der wird synchron weitergeleitet, nie persistiert (wörtliche Umsetzung von Konzept 7.4: "protokolliert nur Metadaten des Vermittlungsvorgangs ... nicht die Dokumentinhalte selbst").

## Vertrauensmodell (ADR 0028)

- **Installation → Hub**: Bearer-API-Key, beim Hub nur gehasht gespeichert (SHA-256).
- **Hub → Installation**: der Hub signiert jede Zustellung mit seinem eigenen, einmalig generierten Schlüsselpaar (RSA-PSS/SHA-256, `X-Federation-Hub-Signature`) — die Zielinstallation verifiziert mit dem bei der eigenen Registrierung einmalig abgerufenen öffentlichen Hub-Schlüssel. Kein geteiltes Geheimnis, das der Hub im Klartext speichern müsste.
- **Ende-zu-Ende-Verschlüsselung der Nutzdaten**: liegt vollständig bei den Installationen (`workflow_service.federation_crypto`) — der Hub leitet `encrypted_payload`/`encrypted_result` nur als opaken String weiter.
- **Versionskompatibilität**: `POST /handovers` prüft beidseitig `(major, minor)`-Zahlenpaare (`repository.is_version_compatible`), lehnt inkompatible Kombinationen mit `409` ab.
- Bewusste Grenzen dieses Grundgerüsts (siehe "Offene Punkte").

## Erst beim Live-Smoke-Test gefundene Bugs (Selbst-Loopback, siehe ADR 0028)

Der beim Sessionstart vereinbarte Selbst-Loopback-Smoke-Test (eine Installation übergibt an sich selbst) deckte zwei reale, mit reiner Unit-Testabdeckung nicht sichtbare Bugs auf — beide entstehen durch die synchrone Zustellung, die im Selbst-Loopback-Fall bis zurück in dieselbe Installation/denselben Prozess reicht:

1. **Postgres-Transaktionsisolation**: `POST /handovers` legte die Handover-Zeile ursprünglich erst *nach* dem Zustellversuch committet an. Im Selbst-Loopback ruft die Zielinstallation aber synchron zurück in den Hub (`POST .../result`), bevor die ursprüngliche Transaktion committet ist — die neue, separate Anfrage sah die Zeile noch nicht (`404`). Fix: `session.commit()` direkt nach dem Anlegen, **vor** dem Zustellversuch.
2. **`handover_id`-Erzeugungsreihenfolge**: der Hub generierte `handover_id` ursprünglich selbst und gab sie erst in der Antwort zurück. Im Selbst-Loopback braucht aber bereits die *eigene, lokale* Buchführung der Absenderinstallation (ihre `FederationTask`-Zeile) diese ID, bevor der verschachtelte Rückruf (`/federation/inbound-result`) sie dort nachschlagen kann — ein klassischer zirkulärer Abhängigkeitsfall. Fix: die Absenderinstallation erzeugt `handover_id` selbst (UUID) und schickt sie im `POST /handovers`-Body mit, committet ihre eigene Zeile lokal **vor** dem Hub-Aufruf. Nebeneffekt: ein sauberes, idempotenzschlüsselartiges Protokoll, das auch für echte Zwei-Installationen-Szenarien sinnvoll ist (der Aufrufer bestimmt seine eigene Referenz, statt auf eine serverseitig generierte ID warten zu müssen).

Beide Fixes sind reine Ablauf-/Protokolländerungen ohne Auswirkung auf die eigentliche Vermittlungslogik — für ein echtes Zwei-Installationen-Szenario (keine Selbst-Referenz) wären beide Reihenfolgeprobleme ohnehin nie aufgetreten, da dort zwei komplett getrennte Prozesse/Datenbanken beteiligt sind.

## Nutzung (seit P6-S9)

Einziger Aufrufer aktuell: `workflow-service` (`federation_client.py`) — registriert sich beim eigenen Start (opt-in über `DMS_FEDERATION_HUB_BASE_URL`), löst darüber föderierte BPMN-Schritte (`taskType=federated`/`federated_return`) aus. Siehe `docs/services/workflow-service.md` "Federation" für die Gegenseite des Protokolls.

## Sensoren (Konzept 10.1)

Noch keine — Monitoring/Sensor-Konzept folgt in Phase 11 (wie bei jedem anderen Service).

## Tests

`uv run pytest services/federation-hub-service/tests` (**21 Tests**): Registrierung/Update mit/ohne korrekten API-Key, Deregistrierung, Adressbuch-Lookup per API-Key, Versionskompatibilitäts-Matrix (parametrisiert), Hub-Identity-Singleton-Idempotenz, echte HTTP-Zustellung an einen In-Prozess-Stub-Empfänger (`httpx.ASGITransport`, verifiziert die Hub-Signatur tatsächlich mit dem öffentlichen Schlüssel aus `GET /public-key`), deterministischer `delivery_failed`-Fall bei einem unerreichbaren Ziel (`httpx.MockTransport`), Ergebnisrückmeldung nur durch die tatsächliche Zielinstallation. Kein Mocking der eigenen Geschäftslogik — nur die *ausgehende* HTTP-Zustellung wird pro Test gezielt gestubbt/simuliert, da ein echter zweiter Netzwerk-Teilnehmer für Unit-Tests nicht sinnvoll aufsetzbar ist.

## Offene Punkte

- **Kein Alembic/keine Migrationen** — wie jeder andere Service dieser frühen Phase, additive Tabellen über `create_all`.
- **`supported_process_types`/`supported_document_types` werden nicht durchgesetzt** — der Hub speichert sie nur, prüft aber nicht, ob ein `POST /handovers` tatsächlich zu einem der von der Zielinstallation deklarierten Typen passt. Wäre eine sinnvolle künftige Härtung (klare Fehlermeldung statt eines `delivery_failed`, das die Zielinstallation erst nach Zustellung selbst ablehnt).
- **API-Key ohne Rotation/Revocation-Mechanismus** außer vollständigem Deregistrieren+Neuregistrieren.
- **Kein mTLS/keine echte Installations-Identität** — die Vertrauensbasis ist bewusst einfach (siehe ADR 0028), wird verfeinert, sobald die Mehrfachinstallations-/Installations-ID-Grundlagen aus P13-S1 existieren (P13-S4).
- **Synchrone Zustellung ohne Retry/Warteschlange** — ein vorübergehend nicht erreichbares Ziel führt sofort zu `delivery_failed`, keine spätere automatische Nachzustellung. Für ein Grundgerüst akzeptiert, siehe `docs/services/workflow-service.md` "Offene Punkte" zur Absenderseite.
- **Keine Bereinigung alter `handover`-Metadatenzeilen** — wächst unbegrenzt, ähnliches Muster wie bei `registry-service`s nie bereinigten inaktiven Instanzen.
- **Kein Sensor-/Monitoring-Anschluss** (folgt Phase 11).
