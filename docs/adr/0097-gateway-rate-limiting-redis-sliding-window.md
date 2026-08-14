# 0097 — Gateway: Rate Limiting auf Redis umgestellt (Sorted-Set-Sliding-Window)

**Status:** akzeptiert
**Kontext:** Konzept 3.5, Session P25-S3 (Post-Roadmap-Feature, revisit von ADR 0005)

## Entscheidung

1. `gateway-service`s `RateLimiter` speichert seinen Sliding-Window-Zähler je Client-Schlüssel
   nicht mehr in einem in-process `dict[str, deque[float]]` (ADR 0005), sondern in **Redis** — dem
   ersten Redis-Vorkommen in diesem gesamten Projekt. Neuer `redis`-Service in
   `infra/docker-compose.yml`, neue `redis_url`-Einstellung (Default zeigt auf diesen Service),
   neue `redis`-Paketabhängigkeit (`redis.asyncio`, der offizielle async Redis-Client — die
   frühere separate `aioredis`-Bibliothek ist seit Redis-py 4.2 darin aufgegangen).
2. Die Sliding-Window-Semantik bleibt exakt erhalten (`max_requests`/`window_seconds`
   unverändert), umgesetzt über ein Sorted Set pro Client-Schlüssel (`ZADD`/`ZREMRANGEBYSCORE`/
   `ZCARD` in einer MULTI/EXEC-Transaktion) statt der einfacheren Fixed-Window-Variante
   (`INCR`+`EXPIRE`).
3. `RateLimiter.allow()` ist jetzt `async` (Redis-Zugriffe sind inhärent asynchron) — der einzige
   Aufrufer (`gateway_service.main.proxy()`) ruft ihn entsprechend mit `await` auf.

## Begründung

- **Redis statt weiterhin in-process**: ADR 0005 hat diese Grenze von Anfang an bewusst
  dokumentiert und explizit als spätere Umstellung vorgezeichnet ("bei Bedarf später auf einen
  externen Store (Redis) umstellen, ohne die `RateLimiter`-Schnittstelle selbst zu ändern") — genau
  dieser Fall tritt jetzt ein: eine horizontal skalierte Gateway-Bereitstellung (mehrere Replikas
  hinter einem Load Balancer) hätte mit dem alten in-process `dict` pro Replika ein eigenes,
  unabhängiges Limit geführt. Ein Client hätte das konfigurierte Limit durch Verteilung seiner
  Anfragen über mehrere Replikas hinweg faktisch vervielfachen können — bei einem Login-Schutz
  (das Rate Limiting gilt explizit auch für die öffentliche `auth-service:login`-Route, siehe
  `docs/services/gateway-service.md`) eine reale Brute-Force-Schwäche, nicht nur eine
  Kapazitätsfrage.
- **Sorted-Set-Sliding-Window statt Fixed-Window (`INCR`+`EXPIRE`)**: Ein Fixed-Window ist
  einfacher (ein Zähler + eine TTL statt eines Sorted Sets), hat aber eine bekannte Schwäche an der
  Fensterkante — das Ende von Fenster N und der Anfang von Fenster N+1 fallen für einen Client
  zeitlich zusammen, wodurch kurzzeitig bis zu `2 × max_requests` durchgelassen werden können.
  Für einen Login-Schutz ist das eine reale, nicht nur theoretische Schwäche. Der Sorted-Set-Ansatz
  bildet die ursprüngliche `deque`-Semantik (jeder einzelne Hit hat einen eigenen Zeitstempel,
  alte Hits fallen exakt nach `window_seconds` heraus) nahezu 1:1 nach — der Preis dafür ist ein
  Sorted-Set-Member pro Request statt eines einzelnen atomaren Zählers, bei den hier üblichen
  Fenstern (Default 600 Requests/60s je Client) vernachlässigbar gegenüber der saubereren Garantie.
  Ein serverseitiges Lua-Skript (für eine einzige atomare Prüf-und-Add-Operation ohne den
  nachträglichen `ZREM`-Fallback bei Überschreitung) wäre eine Alternative gewesen, bringt aber
  einen weiteren beweglichen Teil (Skript-Deployment/-Versionierung) für denselben Effekt — bei den
  hier moderaten Lastanforderungen nicht gerechtfertigt.
- **`redis.asyncio` statt separatem `aioredis`-Paket**: `aioredis` ist seit Redis-py 4.2 offiziell
  in `redis-py` aufgegangen (`from redis import asyncio as redis`) — kein Grund, eine inzwischen
  archivierte separate Abhängigkeit einzuführen.
- **Kein Redis-Persistenz-Volume** (`--save ""`, kein AOF, siehe `infra/docker-compose.yml`): die
  Rate-Limit-Daten sind bewusst rein transient (TTL je Client-Key) — ein Neustart des `redis`-
  Containers selbst setzt das Limit unschädlich zurück (kurzzeitig großzügiger, kein
  Sicherheitsproblem), es gibt keinen fachlichen Grund, dafür Festplatten-I/O oder ein Volume zu
  bezahlen, anders als z. B. NATS JetStream oder Postgres in diesem Stack.

## Konsequenzen

- Neue Infrastruktur-/Deployment-Abhängigkeit: eine echte Installation muss künftig einen `redis`-
  Dienst betreiben, den es vorher nicht gab — für den gebündelten Dev-/Test-Stack ist das durch den
  neuen `infra/docker-compose.yml`-Service bereits abgedeckt, für eine produktive Bereitstellung
  (z. B. Kubernetes) muss ein Betreiber diesen zusätzlichen Baustein einplanen.
- `gateway-service`s einzige verbleibende Rolle als "zustandsloser" Dienst (siehe
  `docs/services/gateway-service.md`, "Eigenes Postgres-Schema: keines") gilt weiterhin für
  Postgres — die Rate-Limit-Daten liegen jetzt in einem geteilten, aber bewusst nicht dauerhaften
  Store, kein neues Postgres-Schema nötig.
- Tests laufen jetzt gegen einen echten, laufenden `redis`-Container (kein Mock, gleiche
  Teststrategie wie gegen Postgres/NATS/MinIO überall sonst im Projekt) — `scripts/run-tests.sh`
  benötigt dafür keine Anpassung, da `redis` wie jeder andere Compose-Service bereits vor dem
  Testlauf hochgefahren wird und `gateway-service` selbst nicht in der `CONSUMER_SERVICES`-Liste
  steht (kein eigener NATS-Konsument, kein Container-Stop/Start-Sonderfall nötig).
- Jeder proxied Request verursacht jetzt zusätzlich mehrere Redis-Roundtrips (eine MULTI/EXEC-
  Transaktion, im Ablehnungsfall ein weiterer `ZREM`) statt eines reinen In-Memory-Zugriffs — bei
  den hier üblichen Lastanforderungen (Dev-/Lern-Projekt, keine Hochlast-Produktion) nicht
  spürbar, aber ein bewusst in Kauf genommener Overhead, den eine echte Hochlast-Installation im
  Auge behalten sollte.
- Instanzauswahl (`InstanceResolver.pick()`, zufälliges Load Balancing) bleibt unverändert reines
  In-Process-Verhalten (siehe P25-S4, parallel in Arbeit) — diese Session ändert ausschließlich das
  Rate Limiting, keine anderen Teile von `main.py`.
