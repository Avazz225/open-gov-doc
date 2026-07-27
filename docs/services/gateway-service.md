# gateway-service

**Verantwortung:** Zentrales API-Gateway/BFF — einziger vorgesehener öffentlicher Einstiegspunkt: Bearer-Token-Validierung, Rate Limiting, dynamisches Routing zu Backend-Services über die Registry (Konzept 3.5).

**Konzept-Referenz:** 3.5
**Eigenes Postgres-Schema:** keines (zustandslos)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `ANY` | `/api/{service_type}/{path:path}` | Proxy zu einer über die Registry aufgelösten, gesunden Instanz von `service_type` |
| `GET` | `/healthz` | Eigener Health-Check |

## Routing

Fragt `GET {registry-service}/instances/{service_type}` ab (Cache-TTL
`instance_cache_ttl_seconds`, Default 5s), wählt zufällig eine gesunde
Instanz und reicht Methode, Query-String, Body und Header (minus Hop-by-Hop-
Header: `Connection`, `Keep-Alive`, `Transfer-Encoding`, `Host`, ...)
unverändert an `{instance.address}/{path}` weiter. Kein registriertes/gesundes
Ziel → `503`. Downstream nicht erreichbar → `502`.

## Auth-Validierung

JWT-Prüfung gegen Keycloak-JWKS (wie im Auth Service, 4.4), zentral für alle
proxied Requests — mit Ausnahme der in `settings.public_routes` gelisteten
Routen (Default: `auth-service:login`, `auth-service:refresh`, da man dafür
erst einen Token braucht). Bei Erfolg werden die Identitäts-Claims als
`X-DMS-Principal`/`X-DMS-Username`/`X-DMS-Roles`-Header an den Downstream
weitergereicht (aktuell von keinem Backend-Service konsumiert, siehe
[ADR 0005](../adr/0005-gateway-registry-routing-and-inprocess-rate-limiting.md)).

## CORS

Die Browser-Frontends (`user-ui`, `admin-ui`) laufen auf einer anderen Origin als das Gateway — `CORSMiddleware` erlaubt `settings.cors_allowed_origins` (Default: `http://localhost:3000`/`:3001`, die Standard-Ports beider Frontends), `allow_credentials=False` (Auth läuft über den `Authorization`-Header, nicht über Cookies). **Zwischenfall & Fix**: Fehlte zunächst komplett — curl-basierte Verifikation deckte das nicht auf, da curl keinen Preflight-`OPTIONS`-Request auslöst. Im echten Browser scheiterte dadurch bereits der Login mit `405` auf den Preflight, bevor der eigentliche `POST /login` überhaupt gesendet wurde. Bei geänderten `USER_UI_PORT`/`ADMIN_UI_PORT` muss `DMS_CORS_ALLOWED_ORIGINS` (JSON-Array) entsprechend mit angepasst werden.

## Rate Limiting

In-Prozess-Sliding-Window je Client (`sub`-Claim bei authentifizierten,
sonst Client-IP), `rate_limit_max_requests`/`rate_limit_window_seconds`
(Default 120/60s). Gilt auch für öffentliche Routen (Login-Schutz). Bei
Überschreitung `429`. Rein lokaler Zähler, keine geteilte Zählung über
mehrere Gateway-Instanzen hinweg — dokumentierte Grenze, siehe ADR 0005.

## Events

Publiziert/konsumiert keine eigenen Events — reiner synchroner Proxy.

## Selbst-Registrierung

Registriert sich **nicht** selbst bei der Registry — es ist der
Einstiegspunkt, an dem Clients direkt über einen festen veröffentlichten Port
ankommen, nicht etwas, das andere Services nachschlagen müssen.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Offene Punkte

- Identitäts-Header (`X-DMS-*`) werden von Backend-Services noch nicht
  konsumiert — Endpunkte mit Principal-Bedarf nehmen ihn weiterhin explizit
  als Parameter entgegen (z. B. `permission-service`s `/check`).
- Backend-Service-Ports sind in der Docker-Compose-Umgebung weiterhin direkt
  veröffentlicht (Entwickler-Komfort) — ein echtes Netzwerk-Perimeter, das
  Backends ausschließlich über das Gateway erreichbar macht, ist ein späterer
  Deployment-Schritt.
- Rate Limiting ohne geteilten Store (siehe ADR 0005) — Redis o. Ä. erst bei
  horizontaler Gateway-Skalierung nötig.
- Kein Least-Connections-/Latenz-bewusstes Load Balancing, nur zufällige
  Instanzauswahl unter mehreren gesunden Kandidaten.
