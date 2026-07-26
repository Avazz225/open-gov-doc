# audit-service

Unveränderliches, hash-verkettetes Ereignisprotokoll (Konzept 3.4/5.3). Konsumiert
alle konfigurierten Event-Bus-Subjects (Default `["registry.>"]`, siehe
`Settings.subjects`) und hängt jedes Ereignis manipulationssicher an eine
Hash-Chain an.

## Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/events?limit=100` | Aufgezeichnete Ereignisse, chronologisch |
| `GET` | `/events/verify` | Prüft die gesamte Kette auf Manipulation |
| `GET` | `/healthz` | Eigener Health-Check |

## Funktionsweise

- Jeder Eintrag: `hash = sha256(prev_hash + kanonisches_JSON(felder))`. Der erste Eintrag verkettet gegen `GENESIS_HASH` (64 Nullen).
- **Idempotent** nach `event_id`: JetStream liefert bei At-least-once-Zustellung ggf. Duplikate — bereits bekannte `event_id`s werden übersprungen, nicht erneut verkettet.
- **Kein `deliver_new`** beim Abonnieren: Der durable Consumer `audit-service` holt nach einem Neustart lückenlos auf, statt Ereignisse zu verpassen (im Gegensatz zu kurzlebigen Test-Abonnements, siehe `dms-eventbus-client`).
- Konsument ohne eigenen Stream (`ensure_stream=False`, siehe [ADR 0001](../../docs/adr/0001-eventbus-consumer-without-stream-ownership.md)) — kennt nur die Subject-Konvention der Producer, nicht deren Stream-Namen.

## Lokale Ausführung

```bash
cd infra && docker compose up -d postgres nats audit-service
curl localhost:8002/healthz
```

## Tests

```bash
cd infra && docker compose up -d postgres nats && cd ..
uv run pytest services/audit-service/tests
```
