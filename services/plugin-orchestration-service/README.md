# plugin-orchestration-service

Plugin Orchestration Service Grundgerüst (Konzept 3.8, P10-S1): Manifest-Format
für "dazustellbare" Elemente (Connectoren, Rendering-Backends, Regel-Plugins,
...), Cold-Start-Platzierung, eigene minimale Ressourcen-Stichprobe. Details
siehe [`docs/services/plugin-orchestration-service.md`](../../docs/services/plugin-orchestration-service.md).

**Grenzen dieser Ausbaustufe** (bewusste Scope-Entscheidungen, siehe
`PROGRESS.md` "Orchestrierung & Rolling Updates"): reine Entscheidungs-/
Empfehlungs-Engine, kein Container-Lifecycle-Manager (kein Docker-Socket-
Zugriff). Mit genau einem gesampelten Knoten ist die "Wahl zwischen Knoten"
(FFD-Bin-Packing über mehrere Knoten, Zeitprofil-Gruppierung, Plattform-
Scheduler-Erkennung, Drain-Mechanismus) noch nicht Gegenstand dieser Session,
folgt in P10-S2/S3.

## Endpunkte

- `POST /plugins/{plugin_type}` — Manifest registrieren/aktualisieren (`admin.orchestration` oder aktivierter Superuser).
- `GET /plugins`, `GET /plugins/{plugin_type}` — Manifeste lesen (ungegatet).
- `POST /plugins/{plugin_type}/resource-usage` — Ressourcen-Selbstmeldung einer laufenden Instanz (ungegatet, service-zu-service).
- `GET /nodes` — gesampelte Knoten (in dieser Umgebung genau einer).
- `POST /placements` — Cold-Start-Platzierungsentscheidung anfordern (`admin.orchestration` oder aktivierter Superuser).
- `GET /placements` — Platzierungshistorie (Audit-Read-Modell, optional `?plugin_type=`).

## Events

- `orchestration.placement.decided` — bei jeder `POST /placements`-Entscheidung, konsumiert von `audit-service` (`orchestration.>`).

## Tests

```bash
uv run pytest services/plugin-orchestration-service/tests
```
