# libs/

Geteilte Python-Pakete, die von mehreren Services genutzt werden — ausdrücklich **keine** geteilte Fachlogik, nur technische Basis (Settings, DB-Setup, Event-Bus-Client, Auth-Client). Jeder Service bleibt fachlich unabhängig (Konzept 3.1).

Wird in P0-S2 angelegt:

- `dms-common/` — Settings, Logging, OpenTelemetry-Basis
- `dms-db-base/` — SQLAlchemy-Async-Setup, Schema-pro-Service-Konvention
- `dms-eventbus-client/` — Publish/Consume-Interface über NATS JetStream (austauschbar, Konzept 3.4)
- `dms-auth-client/` — OIDC/JWT-Validierung gegen Keycloak (Konzept 4.4)
- `dms-constraint-engine/` — zustandslose Objekttyp-Validierung (Konzept 2.2/4.5, seit P3-S3, siehe [ADR 0003](../docs/adr/0003-constraint-engine-as-library.md))
- `dms-registry-client/` — Selbst-Registrierung eines Service bei der Registry inkl. Heartbeat (Konzept 3.2a, seit P4-S1)
- `dms-permission-client/` — HTTP-Client gegen `permission-service` (RBAC-Prüfung, Rollenzuweisung), konsolidiert die zuvor je Service duplizierte `PermissionServiceClient`-Klasse (Post-Roadmap Phase 19 Session 1)
