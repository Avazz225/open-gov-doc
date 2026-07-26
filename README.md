# DMS — Cloud-natives, verteiltes Dokumentenmanagementsystem

Dieses Repository enthält die Umsetzung des folgenden Systems: ein revisionssicheres, verteiltes DMS als Microservice-Architektur (Python/FastAPI-first, Postgres mit Schema-pro-Service, Event-Bus, Plugin-/Connector-Erweiterbarkeit).

## Einstiegspunkte

| Dokument | Zweck |
|---|---|
| Konzept | Fachliches/technisches Konzept — Quelle der Wahrheit für alle Entscheidungen |
| [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) | Session-Roadmap: welche Phase/Session baut was, in welcher Reihenfolge |
| [`PROGRESS.md`](PROGRESS.md) | **Lebender Tracker** — jede neue Arbeitssession startet hier: Status, nächster Schritt, offene Entscheidungen |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Konventionen: Definition of Done, Service-Aufbau, Commit-Stil |
| [`docs/adr/`](docs/adr/) | Architecture Decision Records |
| [`docs/services/`](docs/services/) | Eine Kurzdoku je Service (Verantwortung, API, Schema, Events) |

## Monorepo-Layout

```
services/   # Ein Ordner je Microservice (src/, tests/, Dockerfile, README.md, pyproject.toml)
libs/       # Geteilte Python-Pakete (dms-common, dms-db-base, dms-eventbus-client, dms-auth-client)
infra/      # docker-compose.yml (lokale Dev-Umgebung) + k8s/ (später)
tools/cli/  # DMS-CLI-Tool
docs/       # ADRs + Service-Dokumentation
```

Jeder Service ist unabhängig containerisiert und deploybar (Prinzip aus Konzept 3.1/10.2) — das Monorepo dient nur der Entwicklungsphase (geteilte Konventionen, ein `docker-compose up` für alles).

## Lokale Entwicklungsumgebung

```bash
cd infra
cp .env.example .env   # bei Bedarf Werte anpassen
docker compose up -d
```

Startet Postgres, NATS JetStream, Keycloak und MinIO als Basis-Infrastruktur. Fachliche Services werden ab Phase 1 der Roadmap ergänzt.

## Status

Aktuelle Phase, letzter Stand und nächster Schritt: siehe [`PROGRESS.md`](PROGRESS.md).
