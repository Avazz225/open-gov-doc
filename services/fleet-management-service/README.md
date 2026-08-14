# fleet-management-service

Higher-level, installation-independent management layer for multiple DMS installations (concept 3a, P13-S2).

See [`docs/services/fleet-management-service.md`](../../docs/services/fleet-management-service.md) for endpoints/architecture decisions.

## Summary

- **Not an internal service of an installation** - like `federation-hub-service` (ADR 0028), an independently operated tool for an operator overseeing multiple installations. No access to document contents - only to `registry-service`/`license-service`/`config-service` of a managed installation, via its gateway.
- **Three capabilities** (3a verbatim): health/license overview (`GET /installations/status`), license assignment (`POST /installations/{id}/license`), centralized provisioning from a configuration template (`POST /installations/{id}/provision`).
- Authenticates against a managed installation via an installation-wide key configured there via `DMS_FLEET_AGENT_API_KEY` (P13-S1/S2) - no Keycloak principal of that installation is required.
- **Since P13-S2b: fleet update orchestration** (3a extension) - named groups/waves, versioned update plans, staggered rollouts with the five-valued error decision (`retry_later`/`wait_external`/`manual_required`/`recoverable_failed`/`fatal_contract`). Deliberate boundary: actual execution of risky steps (locking/maintenance mode/update/backup) remains externally/manually confirmed - no remote control of foreign security primitives (see ADR 0038).

## Tests

```bash
uv run --package fleet-management-service pytest services/fleet-management-service/tests
```
