# plugin-orchestration-service

Plugin Orchestration Service skeleton (Concept 3.8, P10-S1): manifest format
for "pluggable" elements (connectors, rendering backends, rule plugins,
...), cold-start placement, own minimal resource sampling. Details
in [`docs/services/plugin-orchestration-service.md`](../../docs/services/plugin-orchestration-service.md).

**Limitations of this stage** (deliberate scope decisions, see
`PROGRESS.md` "Orchestration & Rolling Updates"): pure decision/
recommendation engine, not a container lifecycle manager (no Docker socket
access). With exactly one sampled node, the "choice between nodes"
(FFD bin-packing across multiple nodes, time-profile grouping, platform
scheduler detection, drain mechanism) is not yet part of this session,
follows in P10-S2/S3.

## Endpoints

- `POST /plugins/{plugin_type}` — register/update a manifest (`admin.orchestration` or activated superuser).
- `GET /plugins`, `GET /plugins/{plugin_type}` — read manifests (ungated).
- `POST /plugins/{plugin_type}/resource-usage` — resource self-report from a running instance (ungated, service-to-service).
- `GET /nodes` — sampled nodes (exactly one in this environment).
- `POST /placements` — request a cold-start placement decision (`admin.orchestration` or activated superuser).
- `GET /placements` — placement history (audit read model, optional `?plugin_type=`).

## Events

- `orchestration.placement.decided` — on every `POST /placements` decision, consumed by `audit-service` (`orchestration.>`).

## Tests

```bash
uv run pytest services/plugin-orchestration-service/tests
```
