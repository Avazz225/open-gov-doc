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
`instance_cache_ttl_seconds`, Default 5s), wählt unter den gesunden,
**nicht draining** Instanzen (Drain-Mechanismus, 10.5/3.8, P10-S2 — eine
`status="draining"`-Instanz bleibt erreichbar, bekommt aber keine neuen
Anfragen mehr, siehe `docs/services/registry-service.md`) eine aus (siehe
"Instanzauswahl / Load Balancing" unten) und reicht Methode, Query-String,
Body und Header (minus Hop-by-Hop-Header: `Connection`, `Keep-Alive`,
`Transfer-Encoding`, `Host`, ...) unverändert an `{instance.address}/{path}`
weiter. Kein registriertes/gesundes/aktives Ziel → `503`. Downstream nicht
erreichbar → `502`.

## Instanzauswahl / Load Balancing

**Seit P25-S4 workload-bewusst statt zufällig** (vorher `random.choice`,
ADR 0005; Designentscheidung siehe [ADR 0098](../adr/0098-gateway-workload-aware-instance-selection-per-replica.md)):
`InstanceResolver.pick()` wählt unter den vom Registry-Aufruf
gelieferten Kandidaten die Instanz mit den **wenigsten aktuell offenen
Anfragen** (Instanzen ohne bisherige Reservierung zählen als 0). `proxy()`
reserviert die gewählte Instanz für die Dauer des Upstream-Aufrufs über
`resolver.reserved_instance(instances)` — ein Async-Context-Manager, der
`pick()` aufruft, den Zähler vor dem eigentlichen `http_client.request(...)`
erhöht und ihn in einem `finally` wieder freigibt, also **auch wenn der
Upstream-Aufruf mit einer `httpx.HTTPError` fehlschlägt** (kein dauerhaftes
Leaken eines reservierten Slots). Wer den Zähler außerhalb dieses
Context-Managers manuell steuern will, kann alternativ die beiden
darunterliegenden Methoden `reserve(instance)`/`release(instance)` direkt
verwenden.

**Tie-Break bei mehreren Instanzen mit demselben Minimum**: zufällig unter
den Minimum-Kandidaten, nicht z. B. immer die erste Instanz der Liste —
insbesondere im Ruhezustand (alle Zähler bei 0, etwa direkt nach dem Start)
würde "erste in der Liste" sonst jede Anfrage an dieselbe Instanz schicken,
bis diese als Erste einen offenen Request hätte, statt die Last von Anfang
an gleichmäßig zu streuen.

**Wichtig: rein pro Gateway-Replika, kein clusterweiter Wert.** Der Zähler
offener Anfragen lebt ausschließlich im Prozessspeicher der jeweiligen
`InstanceResolver`-Instanz (`dict[str, int]`, Schlüssel = Instanz-Adresse) —
bei mehreren horizontal skalierten Gateway-Replikas hinter einem Load
Balancer sieht jede Replika nur die Requests, die SIE SELBST gerade an eine
Zielinstanz weiterleitet, nicht die Summe über alle Replikas hinweg. Das ist
eine bewusste Designentscheidung dieser Session, **kein Versehen** — und ein
gewollter Kontrast zum direkt benachbarten P25-S3 (siehe "Rate Limiting"
oben): dort wurde der Zähler bewusst nach Redis verlegt, weil ein rein
lokaler Rate-Limit-Zähler ein Client umgehen könnte, indem er Anfragen über
mehrere Replikas verteilt (ein echtes Sicherheitsproblem). Bei der
Lastverteilung fehlt dieser Umgehungs-Anreiz — im schlimmsten Fall sind die
Instanz-Auswahlen mehrerer Replikas untereinander etwas weniger optimal
abgestimmt, was die Last spürbar, aber nicht sicherheitsrelevant ungleich
verteilt. Eine echte clusterweite Sicht (z. B. ebenfalls über Redis, mit
`INCR`/`DECR` je Instanz-Adresse) wäre möglich, wurde hier aber bewusst NICHT
umgesetzt: der zusätzliche Redis-Roundtrip auf jedem einzelnen proxied
Request (zwei zusätzliche Netzwerk-Hops pro Anfrage, vor UND nach dem
eigentlichen Upstream-Aufruf) steht in keinem praktischen Verhältnis zum
Nutzen bei einem Wert, der ohnehin nur eine Heuristik zur Lastverteilung ist,
nicht eine harte Zugriffsschranke wie beim Rate Limiting.

## Auth-Validierung

JWT-Prüfung, zentral für alle proxied Requests. **Seit Phase 18 Session 2**
([ADR 0064](../adr/0064-superuser-migration-lokale-tokens-gateway-multi-issuer.md)):
`app.state.token_validator` ist ein `MultiIssuerTokenValidator` (neu in
`libs/dms-auth-client`) aus zwei `TokenValidator`-Instanzen — Keycloak-JWKS
(wie zuvor) und `auth-service`s `/.well-known/jwks.json` (neue
`DMS_AUTH_SERVICE_BASE_URL`-Einstellung, direkte Ost-West-Adresse) für
Tokens lokaler technischer Konten (Superuser, künftig Domain-Admins). Ohne
diese Umstellung würde ein frisch lokal eingeloggter Superuser an jedem
proxied Aufruf mit `401` scheitern, obwohl `auth-service` sein eigenes Token
korrekt validiert — live verifiziert (`GET /me` und ein `document-service`-
Aufruf mit einem lokal ausgestellten Token, beide über das echte Gateway,
wurden korrekt durchgelassen). Mit Ausnahme der in `settings.public_routes` gelisteten
Routen (Default: `auth-service:login`, `auth-service:refresh`, da man dafür
erst einen Token braucht; seit P6-S9 zusätzlich die beiden Federation-Hub-
Inbound-Endpunkte; seit P13-S2 zusätzlich vier Routen für den unabhängig
betriebenen `fleet-management-service` — `registry-service:installation`,
`license-service:license/status`, `license-service:license`,
`config-service:config/fleet-import` (**bis P17-S1**: `config-service:
config/import`, siehe "Korrektur" unten), siehe
[ADR 0037](../adr/0037-fleet-management-service-agent-key-and-gateway-public-routes.md);
seit dem Ad-hoc-Post-Roadmap-SSO-Feature zusätzlich `auth-service:oidc/authorize` und
`auth-service:oidc/callback` (der Login-Einstiegspunkt selbst, gleiche Begründung wie
`login`/`refresh` — kein Token vorhanden, bevor die Anmeldung überhaupt stattgefunden hat; beide auch
in `maintenance_mode_allowed_routes`, siehe [ADR 0062](../adr/0062-sso-automatischer-login-oidc-redirect-und-optionales-kerberos.md));
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

**Korrektur an `public_routes` (P17-S1-Fund, [ADR 0058](../adr/0058-konfigurationspakete-manifest-realm-roles-and-gateway-import-route-split.md)):**
`config-service:config/import` teilte sich bis P17-S1 einen einzigen, öffentlichen Pfad zwischen
RBAC-Aufrufern (echte, eingeloggte `config-admin`-Nutzer) und dem Fleet-Agent-Schlüssel (ADR 0037).
Für Pfade in `public_routes` validiert `proxy()` **grundsätzlich keinen Bearer-Token und setzt
`X-DMS-Principal` nie** — unabhängig davon, ob ein gültiger Token im Request steckt. Das bedeutete:
der RBAC-Zweig von `config-service`s Import-Gate war für JEDEN Aufruf dieses Pfads über den
Gateway faktisch unerreichbar, auch für echte Admins — erst bei der ersten Admin-UI-Anbindung von
`config-service` (P17-S1, bis dahin gab es dafür keine Frontend-Seite) gefunden. Fix: eigener,
weiterhin öffentlicher Pfad `config-service:config/fleet-import` ausschließlich für den
Fleet-Agent; `config-service:config/import` ist seither ein regulärer, Token-pflichtiger Pfad. Test
`test_config_import_route_now_requires_gateway_auth_check` bestätigt explizit, dass dieser Pfad
seither einen Bearer-Token verlangt.

## Not-Shutdown / Wartungsmodus (4.8, seit P6-S6)

`proxy()` fragt zu Beginn jedes Requests (vor dem `public_routes`-Check) über einen neuen `MaintenanceStateClient` den Wartungsmodus-Status des Permission Service ab — analog zum `InstanceResolver`-Muster über die Registry aufgelöst statt über eine feste URL, mit kurzem Caching (`maintenance_cache_ttl_seconds`, Default 5s). Ist der Wartungsmodus aktiv, wird jeder Request außerhalb von `settings.maintenance_mode_allowed_routes` (Login/Refresh/Me/Superuser-Status, Permission-Service-Maintenance-Mode-Status/-Lift) mit `503` abgelehnt. Schlägt die Statusabfrage selbst fehl (Permission Service unerreichbar), **fällt der Client offen** (letzter gecachter Wert, Default `false`) — ein unerreichbarer Permission Service soll nicht den gesamten proxied Verkehr blockieren.

Auf jeden durchgelassenen Request (auch außerhalb des Wartungsmodus) wird zusätzlich ein `X-DMS-Maintenance-Active: true`/`false`-Header mitgegeben — Backend-Services, die selbst auf den Zustand reagieren müssen (`auth-service`s `/login`, `workflow-service`s Instanzstart/Task-Abschluss), lesen ihn direkt statt eine eigene Polling-Verbindung zum Permission Service aufzubauen (Header-Broadcast-Muster statt N×Polling, siehe [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md)). **Der Login-Aufruf selbst muss folglich über das Gateway laufen**, damit der Wartungsmodus-Header überhaupt gesetzt wird — ein direkter Aufruf des Auth Service am Gateway vorbei sieht den Header nie und ist damit vom Wartungsmodus nicht betroffen (dieselbe strukturelle Grenze wie bei den direkt veröffentlichten Backend-Ports, ADR 0005).

## CORS

Die Browser-Frontends (`user-ui`, `admin-ui`) laufen auf einer anderen Origin als das Gateway — `CORSMiddleware` erlaubt `settings.cors_allowed_origins` (Default: `http://localhost:3000`/`:3001`, die Standard-Ports beider Frontends), `allow_credentials=False` (Auth läuft über den `Authorization`-Header, nicht über Cookies). **Zwischenfall & Fix**: Fehlte zunächst komplett — curl-basierte Verifikation deckte das nicht auf, da curl keinen Preflight-`OPTIONS`-Request auslöst. Im echten Browser scheiterte dadurch bereits der Login mit `405` auf den Preflight, bevor der eigentliche `POST /login` überhaupt gesendet wurde. Bei geänderten `USER_UI_PORT`/`ADMIN_UI_PORT` muss `DMS_CORS_ALLOWED_ORIGINS` (JSON-Array) entsprechend mit angepasst werden.

## Rate Limiting

Sliding-Window je Client (`sub`-Claim bei authentifizierten, sonst Client-IP),
`rate_limit_max_requests`/`rate_limit_window_seconds` (Default **600/60s**,
siehe unten). Gilt auch für öffentliche Routen (Login-Schutz). Bei
Überschreitung `429`.

**Seit P25-S3 ([ADR 0097](../adr/0097-gateway-rate-limiting-redis-sliding-window.md))
geteilter Redis-Store statt in-process `dict`**: ursprünglich (ADR 0005) ein
rein lokaler Zähler ohne geteilte Zählung über mehrere Gateway-Instanzen
hinweg — bei horizontaler Skalierung des Gateways (mehrere Replikas hinter
einem Load Balancer) hätte ein Client das Limit durch Verteilung seiner
Anfragen über mehrere Replikas faktisch vervielfachen können. `RateLimiter`
speichert den Zähler jetzt in Redis (neue `redis_url`-Einstellung, neuer
`redis`-Service in `infra/docker-compose.yml`, Default `redis://redis:6379/0`
in der Compose-Umgebung) — alle Gateway-Instanzen sehen denselben Zähler je
Client-Schlüssel. `allow()` ist entsprechend `async` (Redis-Zugriffe sind
inhärent asynchron), der einzige Aufrufer in `proxy()` ruft ihn mit `await`
auf.

**Sliding Window via Sorted Set statt Fixed Window**: umgesetzt über ein
Redis Sorted Set pro Client-Schlüssel (`ZADD`/`ZREMRANGEBYSCORE`/`ZCARD` in
einer MULTI/EXEC-Transaktion), nicht über die einfachere Fixed-Window-
Variante (`INCR`+`EXPIRE`). Ein Fixed-Window lässt an der Fensterkante
kurzzeitig bis zu `2 × max_requests` durch (Ende von Fenster N und Anfang von
Fenster N+1 fallen für einen Client zeitlich zusammen) — für einen
Login-Schutz eine reale Schwäche. Der Sorted-Set-Ansatz bildet die
ursprüngliche `deque`-Semantik nahezu 1:1 nach; der Preis ist ein
Sorted-Set-Member pro Request statt eines einzelnen Zählers, bei den hier
üblichen Fenstern vernachlässigbar. Details/Alternativen-Abwägung siehe
ADR 0097.

Redis ist in diesem Stack bewusst ohne Persistenz betrieben (`--save ""`,
kein AOF, kein Volume) — die Rate-Limit-Daten sind rein transient (TTL je
Client-Key), ein Neustart von Redis selbst setzt das Limit unschädlich
zurück. Live verifiziert (P25-S3): das Limit auf 3 Requests/120s gesenkt,
vierte Anfrage lieferte `429`; anschließend den `gateway-service`-Container
neu gestartet (frischer Prozess, neue `RateLimiter`-Instanz) — die
unmittelbar folgende Anfrage desselben Clients lieferte weiterhin `429`,
belegt also, dass der Zähler tatsächlich in Redis lebt und nicht in einem
neuen In-Prozess-Cache landet.

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
- Seit P25-S4 zwar Least-Open-Connections-Auswahl statt zufälliger
  Instanzauswahl (siehe "Instanzauswahl / Load Balancing" oben), aber
  weiterhin **nicht latenz-bewusst** — eine Instanz, die zwar wenige offene
  Anfragen, aber eine hohe Antwortzeit hat, wird dadurch nicht erkannt/
  gemieden. Der Zähler ist außerdem rein pro Gateway-Replika (kein
  clusterweiter Wert, bewusst so — siehe oben), anders als der seit P25-S3
  über Redis geteilte Rate-Limit-Zähler.
- Redis läuft im gebündelten Dev-/Test-Stack ohne Auth/TLS (dev-only, gleiche
  Haltung wie Postgres/NATS/MinIO in diesem Stack) — eine echte Installation
  müsste eigene Zugangsdaten/Netzwerksegmentierung für den `redis`-Dienst
  vorsehen, das ist hier nicht modelliert.
