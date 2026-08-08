# registry-service

**Verantwortung:** Service Discovery — Registrierung, Heartbeat, aktive Routingtabelle je Servicetyp (Konzept 3.2a). Seit P9-S2 zusätzlich Lizenzvermittlung (3.2b/9.3): fragt `license-service` ab und reicht einen berechneten Lizenzstatus (`licensed`/`demo`/`unlicensed`) je `service_type` an registrierende/heartbeatende Services weiter, ohne selbst eine Lizenzprüfung durchzuführen. Seit P10-S2 zusätzlich der **Drain-Mechanismus** (10.5/3.8): eine Instanz kann als `draining` markiert werden, bleibt dabei erreichbar, bekommt aber keine neuen Anfragen mehr über das Gateway.

**Konzept-Referenz:** 3.2a, 3.2b, 9.3, 10.5
**Eigenes Postgres-Schema:** `registry` (Tabelle `service_instance`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/instances` | Registrieren/Aktualisieren (Upsert nach `instance_id`) |
| `POST` | `/instances/{instance_id}/heartbeat` | Heartbeat, aktualisiert `last_heartbeat_at` |
| `DELETE` | `/instances/{instance_id}` | Deregistrieren |
| `POST` | `/instances/{instance_id}/drain` | Drain-Mechanismus (10.5/3.8, P10-S2): setzt `status="draining"` — ungegatet, WANN gedraint wird entscheidet ein externes Deploy-Werkzeug/`scripts/rolling-update.sh`, nicht die Registry selbst. |
| `POST` | `/instances/{instance_id}/activate` | Umkehrung von `/drain` (10.5, P10-S3): setzt `status="active"` zurück — Grundlage für einen echten Rollback-Pfad, siehe `docs/operations/rolling-updates.md`. |
| `GET` | `/instances/{service_type}` | Nur aktuell erreichbare Instanzen dieses Typs |
| `GET` | `/instances` | Alle Instanzen inkl. berechnetem `healthy`-Flag |
| `GET` | `/license-status/{service_type}` | Berechneter Lizenzstatus (`licensed`/`demo`/`unlicensed`) für diesen Servicetyp — ungegatet, für interne Poll-Clients (z. B. `workflow-service`, siehe unten). |
| `GET` | `/metrics` | Eigene Sensoren im Prometheus-Format (10.1, P11-S1) — wird von `monitoring-service` gescraped, nicht direkt von Prometheus. |
| `GET` | `/healthz` | Eigener Health-Check |

## Datenmodell

`service_instance`: `instance_id` (PK), `service_type`, `version`, `capabilities` (JSON-Liste), `sensors` (JSON-Liste, seit P11-S1 — rein durchgereichte Sensor-Selbstdeklaration, siehe unten), `health_endpoint`, `address`, `registered_at`, `last_heartbeat_at`, `status` (`"active"`/`"draining"`, Default `"active"`, seit P10-S2 — additiv per `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` nachgerüstet, kein Alembic in dieser Phase). `healthy` ist kein gespeichertes Feld, sondern wird bei jeder Abfrage aus `last_heartbeat_at` vs. `heartbeat_timeout_seconds` (Default 15s, konfigurierbar über `DMS_HEARTBEAT_TIMEOUT_SECONDS`) berechnet. `license_status` (in `InstanceOut`) ist ebenfalls kein gespeichertes Feld, sondern wird bei jeder Antwort per `ComponentLicenseCache` nachgetragen.

## Drain-Mechanismus (10.5/3.8, P10-S2/S3)

- **Zustand, keine automatische Auslösung**: `POST /instances/{instance_id}/drain` setzt `status="draining"` — die Registry entscheidet selbst nicht, *wann* gedraint wird. Das übernimmt seit P10-S3 `scripts/rolling-update.sh`, das denselben Mechanismus für Update-Rollouts wiederverwendet, statt ihn neu zu bauen (Konzept 10.5: "denselben Drain-Mechanismus... nur zu einem anderen Anlass").
- **Wirkung ausschließlich beim Routing**: eine `draining`-Instanz bleibt in `GET /instances/{type}` sichtbar (nicht deregistriert, kein Kill), verschwindet aber aus dem Auswahl-Pool des Gateways für **neue** Anfragen (`gateway_service.upstream.InstanceResolver.resolve()` filtert zusätzlich zu `healthy` auf `status == "active"`). Bereits laufende Anfragen sind nie betroffen — entspricht 10.5 wörtlich ("nimmt keine neuen Aufgaben mehr an, schließt aber laufende Vorgänge ab").
- **Ungegatet**, wie jeder andere Registry-Endpunkt — die Registry hat nirgends ein Rollen-Gate, das wäre an dieser Stelle keine Konsistenzverbesserung.
- Eine **neue** Registrierung (Zeile existiert noch nicht) startet immer mit `status="active"`; eine Re-Registrierung derselben `instance_id` (Selbstheilung nach `404`, kein echter Neustart, siehe `dms-registry-client`) lässt einen bestehenden `status` unverändert — nur Heartbeat/Register ändern ihn nie automatisch zurück, ausschließlich `/drain`/`/activate` setzen ihn.
- **Rollback (10.5, P10-S3)**: `POST /instances/{instance_id}/activate` setzt `status` zurück auf `"active"` — ohne diese Umkehrung gäbe es keinen Weg, eine bereits gedrainte Instanz wieder ansprechbar für neue Anfragen zu machen. Konzept 10.5 verlangt ausdrücklich, dass ein Rollback möglich bleibt, solange der Drain noch nicht vollständig abgeschlossen (die Instanz also noch nicht gestoppt) ist. Genutzt von `scripts/rolling-update.sh`s manuellem Rollback-Verfahren, siehe `docs/operations/rolling-updates.md`.

## Lizenzvermittlung (3.2b/9.3, P9-S2)

- **Nur konfigurierte Komponenten sind überhaupt lizenzpflichtig**: `settings.licensable_components` (Default `{"workflow-service": "demo"}`) ordnet jedem separat lizenzierbaren `service_type` eine Policy zu (`"demo"` oder `"lock"`), die greift, wenn keine gültige Lizenz installiert ist oder die Komponente nicht in `licensed_components` der Lizenz enthalten ist. Jeder nicht gelistete `service_type` ist "core" und bekommt immer `"licensed"` — Konzept 9.1 nennt CMIS-Connector/Migration-Service/Workflow-Automatisierung nur als Beispiele, nicht "jeder Service ist lizenzpflichtig".
- **`ComponentLicenseCache`** (`licensing.py`): TTL-Cache (`license_status_cache_ttl_seconds`, Default 60s) um den rohen `license-service`-Status, plus Invalidierung durch den neuen `license.>`-NATS-Konsumenten (`consumer.py`, erster eigener Konsument dieses Service, durable `registry-service`) — reagiert damit sowohl ereignisgetrieben als auch mit einer harten Obergrenze auf Statusänderungen.
- `InstanceOut` (Register-/Heartbeat-/Listing-Antworten) trägt zusätzlich `license_status`. Ein dedizierter `GET /license-status/{service_type}` erlaubt bereits laufenden Services, ihren eigenen Status ohne Neustart erneut abzufragen (z. B. `workflow-service`s `license_client.LicenseStatusClient`, siehe `docs/services/workflow-service.md`).
- Fail-open bei nicht erreichbarem `license-service` (`"licensed"` für core, konfigurierte Policy für licensierbare Komponenten bleibt zuletzt bekannter Wert) — ein Lizenzdienst-Ausfall soll die Registry nicht lahmlegen.

## Events

Publiziert (Stream `registry`, `dms-eventbus-client`, nach Commit):

- `registry.instance.registered` — `subject`=`instance_id`, `payload`={`service_type`, `version`}
- `registry.instance.deregistered` — `subject`=`instance_id`, `payload`={`service_type`}

Kein Event pro Heartbeat. Konsumiert wird dieser Strom vom Audit Service (`docs/services/audit-service.md`). Seit P9-S2 konsumiert `registry-service` selbst `license.>` (siehe oben) — reiner Cache-Invalidierungs-Trigger, kein Payload-Parsing.

## Nutzung (seit P4-S1)

Bis P4-S1 existierte nur diese API, ohne einen einzigen Aufrufer. Seitdem
registrieren sich sieben Backend-Services selbst hier (über die neue geteilte
Lib `libs/dms-registry-client`: Register-beim-Start, periodischer Heartbeat,
Deregister-beim-Shutdown) — siehe `docs/services/gateway-service.md`, das
`/instances/{service_type}` nutzt, um Requests dynamisch weiterzuleiten,
statt Backend-Adressen statisch zu konfigurieren.

**Registriert sich seit P4-S3 auch bei sich selbst** (`DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS` zeigen beide auf die eigene Adresse): Ohne das gäbe es für `service_type=registry-service` keine auflösbare Instanz, und das Gateway könnte `/api/registry-service/...` (z. B. für die Admin-UI-Registry-Übersicht) nie auflösen. Die allererste Registrierung schlägt dabei unvermeidlich fehl (der eigene Uvicorn-Server nimmt erst nach Abschluss des Lifespan-Startups Verbindungen an) — der Selbstheilungs-Fix aus `dms-registry-client` (Re-Registrierung bei `404` im nächsten Heartbeat, siehe P4-S1) greift hier für den denkbar häufigsten Anwendungsfall dieses Mechanismus.

## Sensoren (Konzept 10.1, P11-S1)

`registry-service` ist selbst einer der zwei Sensor-Piloten (kein Vollretrofit aller Services, siehe P11-S0-Befund): meldet bei der eigenen Selbstregistrierung zwei Sensoren an (`registry.instances.active_total`, `registry.service.heartbeat.miss` — beide Namen wörtlich aus Konzept 10.1s Beispielliste) und exponiert sie über einen eigenen `GET /metrics` (Prometheus-Format, `libs/dms-metrics-client`). **Die eigentliche Sensor-Registry (Katalog-Aggregation + Aktivierungskonfiguration) lebt bewusst NICHT hier**, sondern im neuen `monitoring-service` (P11-S1-Architekturentscheidung nach Rückfrage bei Sessionstart — Prometheus scraped ausschließlich `monitoring-service`, das seinerseits `GET /instances` hier abfragt, um die deklarierten `sensors` jeder Instanz zu lesen und deren `/metrics`-Endpunkte selbst zu scrapen). `registry-service`s Footprint bleibt entsprechend minimal: ein durchgereichtes `sensors`-Feld, keine neue Businesslogik, kein neues Gate. Details siehe `docs/services/monitoring-service.md` und `docs/operations/monitoring.md`.

## Offene Punkte

- Aktives Anpingen des gemeldeten `health_endpoint` (statt reinem Heartbeat-Push) als mögliche spätere Ergänzung, nicht Teil dieser Session.
- **Kein Aufräumen dauerhaft unerreichbarer Instanzen** (seit P4-S1 beobachtet: Container-Neustarts ohne sauberes `DELETE /instances/{id}`, z. B. bei `docker compose down` ohne vorheriges Deregistrieren, hinterlassen dauerhaft `healthy=false`-Zeilen). Unkritisch für das Routing (`GET /instances/{service_type}` filtert sie bereits heraus), sammelt sich aber unbegrenzt in der Tabelle an — eine periodische Bereinigung (z. B. Löschen nach X Tagen ohne Heartbeat) ist nicht Teil dieser Session.
