# gateway-service

**Verantwortung:** Zentrales API-Gateway/BFF — einziger vorgesehener öffentlicher Einstiegspunkt: Bearer-Token-Validierung, Rate Limiting, dynamisches Routing zu Backend-Services über die Registry (Konzept 3.5). Seit P6-S6 zusätzlich zentraler Durchsetzungspunkt für den systemweiten Wartungsmodus (Not-Shutdown, 4.8) — siehe [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md).

**Konzept-Referenz:** 3.5, 4.8
**Eigenes Postgres-Schema:** keines (zustandslos)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `ANY` | `/api/{service_type}/{path:path}` | Proxy zu einer über die Registry aufgelösten, gesunden Instanz von `service_type` |
| `GET` | `/healthz` | Eigener Health-Check |

## Routing

Fragt `GET {registry-service}/instances/{service_type}` ab (Cache-TTL
`instance_cache_ttl_seconds`, Default 5s), wählt zufällig eine gesunde,
**nicht draining** Instanz (Drain-Mechanismus, 10.5/3.8, P10-S2 — eine
`status="draining"`-Instanz bleibt erreichbar, bekommt aber keine neuen
Anfragen mehr, siehe `docs/services/registry-service.md`) und reicht
Methode, Query-String, Body und Header (minus Hop-by-Hop-Header:
`Connection`, `Keep-Alive`, `Transfer-Encoding`, `Host`, ...) unverändert an
`{instance.address}/{path}` weiter. Kein registriertes/gesundes/aktives
Ziel → `503`. Downstream nicht erreichbar → `502`.

## Auth-Validierung

JWT-Prüfung gegen Keycloak-JWKS (wie im Auth Service, 4.4), zentral für alle
proxied Requests — mit Ausnahme der in `settings.public_routes` gelisteten
Routen (Default: `auth-service:login`, `auth-service:refresh`, da man dafür
erst einen Token braucht; seit P6-S9 zusätzlich die beiden Federation-Hub-
Inbound-Endpunkte; seit P13-S2 zusätzlich vier Routen für den unabhängig
betriebenen `fleet-management-service` — `registry-service:installation`,
`license-service:license/status`, `license-service:license`,
`config-service:config/import`, siehe
[ADR 0037](../adr/0037-fleet-management-service-agent-key-and-gateway-public-routes.md);
seit P14-S10 zusätzlich `document-service:public/share-links` und
`document-service:public/share-links/content` für den öffentlichen
Freigabelink (4.2a) — anonyme Betrachter besitzen keinen Bearer-Token dieser
Installation, das eigentliche Zugriffsgeheimnis ist stattdessen das
Freigabelink-Token selbst, das als Query-Parameter mitreist (`?token=...`,
nicht als Pfadsegment) und von `document-service` geprüft wird; dadurch
bleiben diese beiden neuen Einträge einfache, statische Exact-Match-Strings
ohne Wildcard-Matching-Logik am Gateway selbst, siehe
[ADR 0047](../adr/0047-public-share-link-query-param-token-and-disable-semantics.md)).
Bei Erfolg werden die Identitäts-Claims als
`X-DMS-Principal`/`X-DMS-Username`/`X-DMS-Roles`-Header an den Downstream
weitergereicht — ursprünglich (ADR 0005) von keinem Backend-Service
konsumiert, inzwischen aber die Grundlage mehrerer echter Prüfungen
(`search-service`/`teamspace-service` seit P14-S6/P5-S4, `document-service`
seit P14-S10, `permission-service`/`workflow-service` seit P14-S11 u. a.).
Für eine öffentliche `public_routes`-Route lässt der Gateway einen im
Original-Request bereits vorhandenen `Authorization`-Header unverändert an
den Downstream durch (kein Überschreiben mit leeren Identitäts-Headern) -
Grundlage für den Fleet-Agent-Schlüssel-Bypass oben, den nur der jeweilige
Zielservice selbst prüft, nicht der Gateway.

**Sicherheitsfund + Fix (P14-S11-Live-Verifikation, [ADR 0049](../adr/0049-gateway-header-spoofing-fix-strip-client-x-dms-headers.md)):**
bis zu diesem Fund konnte ein Client mit gültigem Bearer-Token einen eigenen
`X-DMS-Principal`/`-Roles`-Header mitschicken, der NICHT vom echten,
JWT-abgeleiteten Wert überschrieben wurde — ein Fall von Groß-/
Kleinschreibungs-Ungleichheit zwischen Python-Dict-Schlüsseln
(`upstream.filter_headers()` behielt die ASGI-normalisierte, kleingeschriebene
Schreibweise des eingehenden Headers bei, `identity_headers` im Quellcode ist
großgeschrieben — beide landeten als zwei separate Header beim Downstream).
`filter_headers()` entfernt seither jeden eingehenden `X-DMS-*`-Header
unabhängig von seiner Schreibweise, bevor die echten Identitäts-Header
gesetzt werden. Live verifiziert: derselbe Spoofing-Versuch liefert seither
nachweislich die echte Identität.

## Not-Shutdown / Wartungsmodus (4.8, seit P6-S6)

`proxy()` fragt zu Beginn jedes Requests (vor dem `public_routes`-Check) über einen neuen `MaintenanceStateClient` den Wartungsmodus-Status des Permission Service ab — analog zum `InstanceResolver`-Muster über die Registry aufgelöst statt über eine feste URL, mit kurzem Caching (`maintenance_cache_ttl_seconds`, Default 5s). Ist der Wartungsmodus aktiv, wird jeder Request außerhalb von `settings.maintenance_mode_allowed_routes` (Login/Refresh/Me/Superuser-Status, Permission-Service-Maintenance-Mode-Status/-Lift) mit `503` abgelehnt. Schlägt die Statusabfrage selbst fehl (Permission Service unerreichbar), **fällt der Client offen** (letzter gecachter Wert, Default `false`) — ein unerreichbarer Permission Service soll nicht den gesamten proxied Verkehr blockieren.

Auf jeden durchgelassenen Request (auch außerhalb des Wartungsmodus) wird zusätzlich ein `X-DMS-Maintenance-Active: true`/`false`-Header mitgegeben — Backend-Services, die selbst auf den Zustand reagieren müssen (`auth-service`s `/login`, `workflow-service`s Instanzstart/Task-Abschluss), lesen ihn direkt statt eine eigene Polling-Verbindung zum Permission Service aufzubauen (Header-Broadcast-Muster statt N×Polling, siehe [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md)). **Der Login-Aufruf selbst muss folglich über das Gateway laufen**, damit der Wartungsmodus-Header überhaupt gesetzt wird — ein direkter Aufruf des Auth Service am Gateway vorbei sieht den Header nie und ist damit vom Wartungsmodus nicht betroffen (dieselbe strukturelle Grenze wie bei den direkt veröffentlichten Backend-Ports, ADR 0005).

## CORS

Die Browser-Frontends (`user-ui`, `admin-ui`) laufen auf einer anderen Origin als das Gateway — `CORSMiddleware` erlaubt `settings.cors_allowed_origins` (Default: `http://localhost:3000`/`:3001`, die Standard-Ports beider Frontends), `allow_credentials=False` (Auth läuft über den `Authorization`-Header, nicht über Cookies). **Zwischenfall & Fix**: Fehlte zunächst komplett — curl-basierte Verifikation deckte das nicht auf, da curl keinen Preflight-`OPTIONS`-Request auslöst. Im echten Browser scheiterte dadurch bereits der Login mit `405` auf den Preflight, bevor der eigentliche `POST /login` überhaupt gesendet wurde. Bei geänderten `USER_UI_PORT`/`ADMIN_UI_PORT` muss `DMS_CORS_ALLOWED_ORIGINS` (JSON-Array) entsprechend mit angepasst werden.

## Rate Limiting

In-Prozess-Sliding-Window je Client (`sub`-Claim bei authentifizierten,
sonst Client-IP), `rate_limit_max_requests`/`rate_limit_window_seconds`
(Default **600/60s**, siehe unten). Gilt auch für öffentliche Routen (Login-Schutz). Bei
Überschreitung `429`. Rein lokaler Zähler, keine geteilte Zählung über
mehrere Gateway-Instanzen hinweg — dokumentierte Grenze, siehe ADR 0005.

**Default nach Nutzer-Feedback von 120 auf 600 angehoben**: der ursprüngliche Default (120
Requests/60s) löste bei ganz normaler interaktiver Nutzung sehr schnell `429` aus — kein Bug in
Client, Auth Service oder Keycloak (alle drei real geprüft und ausgeschlossen), sondern schlicht
zu knapp für dieses SPA bemessen. Ein einzelner Seitenaufruf des Drei-Spalten-Arbeitsbereichs
feuert bereits gut ein Dutzend paralleler Aufrufe (Ordner, Dokumente, Favoriten,
Genehmigungskonfiguration, Kennzeichen-Config, Objekttypen, `auth-service:me`/`me/preferences`),
dazu kommt `MaintenanceBanner`s 30-Sekunden-Poll in beiden Frontends — normales, aktives Klicken
überschreitet 120 Aufrufe pro Minute für einen einzelnen eingeloggten Nutzer (eigener
`sub`-Schlüssel, siehe oben) trivial. Live im laufenden Gateway-Log bestätigt: ein realer Burst
zeigte `429` gleichzeitig auf `document-service`/`folder-service`/`object-type-service`/
`permission-service`/`auth-service`-Routen. 600/60s lässt reale Nutzung deutlich mehr Luft,
bleibt aber ein echtes Sicherheitsnetz gegen tatsächlich außer Kontrolle geratene Clients.

## Events

Publiziert/konsumiert keine eigenen Events — reiner synchroner Proxy.

## Selbst-Registrierung

Registriert sich **nicht** selbst bei der Registry — es ist der
Einstiegspunkt, an dem Clients direkt über einen festen veröffentlichten Port
ankommen, nicht etwas, das andere Services nachschlagen müssen.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Offene Punkte

- Identitäts-Header (`X-DMS-Principal`/`X-DMS-Username`/`X-DMS-Roles`) werden
  weiterhin nicht von JEDEM Backend-Service konsumiert — viele Endpunkte mit
  Principal-Bedarf nehmen ihn weiterhin explizit als Parameter/Body-Feld
  entgegen (z. B. `permission-service`s älterer `/check`, dessen
  `principal_id` als Query-Parameter statt aus dem Header kommt). Echte
  Header-Konsumenten inzwischen: `search-service` (seit P5-S4),
  `teamspace-service`/`auth-service`s `/users/lookup` (seit P14-S6),
  `document-service`s Freigabelink-Endpunkte (seit P14-S10),
  `permission-service`s Delegations-Endpunkte/`workflow-service`s
  Aufgabenabschluss "im Auftrag von" (seit P14-S11). **Bis P14-S11 galt
  dieser Header fälschlich als lückenlos vertrauenswürdig** — ein Client
  konnte ihn selbst mitschicken und damit die echte, JWT-abgeleitete
  Identität überschreiben (behoben, siehe
  [ADR 0049](../adr/0049-gateway-header-spoofing-fix-strip-client-x-dms-headers.md)).
  **Der `X-DMS-Maintenance-Active`-Header (4.8, seit P6-S6) wird von
  zwei Services konsumiert** (`auth-service`s `/login`, `workflow-service`s
  Instanzstart/Task-Abschluss) — siehe "Not-Shutdown / Wartungsmodus" oben.
- Backend-Service-Ports sind in der Docker-Compose-Umgebung weiterhin direkt
  veröffentlicht (Entwickler-Komfort) — ein echtes Netzwerk-Perimeter, das
  Backends ausschließlich über das Gateway erreichbar macht, ist ein späterer
  Deployment-Schritt.
- Rate Limiting ohne geteilten Store (siehe ADR 0005) — Redis o. Ä. erst bei
  horizontaler Gateway-Skalierung nötig.
- Kein Least-Connections-/Latenz-bewusstes Load Balancing, nur zufällige
  Instanzauswahl unter mehreren gesunden Kandidaten.
