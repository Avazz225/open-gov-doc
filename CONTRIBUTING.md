# Konventionen

Dieses Projekt wird über viele einzelne Arbeitssessions hinweg gebaut (siehe [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md)). Diese Konventionen sorgen dafür, dass das Repo dabei durchgehend sauber und nachvollziehbar bleibt — nicht erst am Ende.

## Definition of Done (jede Session, die Code produziert)

1. Der betroffene Service/die Lib hat: `README.md`, Tests (`pytest`), `Dockerfile`, Eintrag in `infra/docker-compose.yml`, strukturiertes Logging.
2. `docs/services/<service>.md` existiert/ist aktuell (Verantwortung, Endpunkte, Schema, Events die er publiziert/konsumiert).
3. Nicht-triviale Architekturentscheidungen sind als kurzes ADR in `docs/adr/` festgehalten (Template siehe [`docs/adr/README.md`](docs/adr/README.md)).
4. `PROGRESS.md` ist aktualisiert: erledigte Session abgehakt, nächste Session benannt, offene Fragen notiert.
5. Bei substantiellem Code-Zuwachs: `graphify dms/ --update` (spätestens am Ende jeder Phase verpflichtend).
6. Tests laufen grün (`pytest` je Service, `docker compose up` startet fehlerfrei).

## Service-Aufbau

Jeder Service unter `services/<name>/`:

```
services/<name>/
  src/<name>/          # Quellcode
  tests/                # pytest
  Dockerfile
  pyproject.toml         # uv-verwaltet
  README.md              # Kurzüberblick, lokale Ausführung
```

Ein Service liest/schreibt ausschließlich sein eigenes Postgres-Schema (Konzept 3.1) und kommuniziert mit anderen Services nur über deren API bzw. den Event-Bus — kein Import fremder Service-Interna.

## Commits

- Ein Commit pro logischem Schritt, aussagekräftige Nachricht (was + warum, nicht nur was).
- Kein `--no-verify`, keine erzwungenen Pushes ohne ausdrückliche Rücksprache.
- `PROGRESS.md`-Update gehört in denselben oder den letzten Commit einer Session, nicht vergessen.

## Branching

Trunk-based auf `main`. Feature-Branches sind optional und bei Bedarf pro Session nutzbar, aber kein Zwang für dieses Solo-/Lern-Projekt.
