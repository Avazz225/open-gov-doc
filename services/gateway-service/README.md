# gateway-service

Zentrales API-Gateway/BFF (Konzept 3.5): einziger öffentlich erreichbarer
Einstiegspunkt, der Bearer-Token validiert, Rate Limiting durchsetzt und
Requests dynamisch über die Registry an Backend-Services weiterleitet, statt
fest verdrahtete Adressen zu nutzen.

## Routing-Konvention

```
ANY /api/{service_type}/{path...}  →  {instance.address}/{path...}
```

`service_type` muss dem `service_type` entsprechen, unter dem sich eine
Instanz bei der Registry registriert hat (z. B. `permission-service`,
`document-service`). Das Gateway fragt `GET {registry}/instances/{service_type}`
ab (kurz gecached, `instance_cache_ttl_seconds`), wählt zufällig eine gesunde
Instanz aus und reicht Methode, Query-String, Body und Header (minus Hop-by-
Hop-Header) unverändert durch.

## Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| `ANY` | `/api/{service_type}/{path:path}` | Proxy zu einer über die Registry aufgelösten Instanz |
| `GET` | `/healthz` | Eigener Health-Check |

## Auth-Validierung

Jeder proxied Request braucht einen gültigen Bearer-Token (JWT gegen Keycloak-
JWKS geprüft, wie im Auth Service, 4.4) — **außer** für Routen in
`settings.public_routes` (Default: `auth-service:login`, `auth-service:refresh`,
denn dafür braucht man ja erst einen Token). Bei Erfolg werden die Identitäts-
Claims als Header an den Downstream weitergereicht:

| Header | Inhalt |
|---|---|
| `X-DMS-Principal` | `sub`-Claim |
| `X-DMS-Username` | `preferred_username`-Claim |
| `X-DMS-Roles` | `realm_access.roles`, kommasepariert |

Backend-Services konsumieren diese Header aktuell noch nicht (jeder Endpunkt,
der einen Principal braucht, z. B. `permission-service`s `/check`, nimmt ihn
weiterhin explizit als Parameter entgegen) — das Verdrahten folgt, sobald eine
UI/BFF-Session diese Header tatsächlich benötigt.

## Rate Limiting

In-Prozess-Sliding-Window je Client (authentifiziert: `sub`-Claim, sonst
Client-IP), `rate_limit_max_requests`/`rate_limit_window_seconds`. Bei
Überschreitung: `429`. Gilt für **alle** Routen, auch öffentliche - der
Login-Endpunkt selbst muss vor Brute-Force geschützt werden.

**Bekannte Grenze**: rein lokaler Zähler, kein geteilter Store. Sobald das
Gateway horizontal skaliert wird, umgeht ein Client das Limit einfach über
mehrere Gateway-Instanzen (siehe [ADR 0005](../../docs/adr/0005-gateway-registry-routing-and-inprocess-rate-limiting.md)).

## Fehlerfälle

| Situation | Antwort |
|---|---|
| Kein/ungültiger Bearer-Token auf geschützter Route | `401` |
| Rate Limit überschritten | `429` |
| Kein gesundes Ziel für `service_type` registriert | `503` |
| Downstream nicht erreichbar/Fehler | `502` |

## Selbst-Registrierung

Das Gateway selbst registriert sich **nicht** bei der Registry - es ist der
Einstiegspunkt, an dem Clients direkt ankommen (fester veröffentlichter Port),
nicht etwas, das andere Services über die Registry nachschlagen.

## Lokale Ausführung

```bash
cd infra && docker compose up -d postgres nats keycloak registry-service gateway-service
curl localhost:8009/healthz
```

## Tests

Gegen echte laufende Infrastruktur (kein Mock der Registry/des Downstreams;
JWT-Prüfung läuft über echte, aber lokal erzeugte Test-Schlüssel statt
gegen einen echten Keycloak, siehe `tests/conftest.py`):

```bash
cd infra && docker compose up -d postgres nats registry-service audit-service && cd ..
uv run pytest services/gateway-service/tests
```
