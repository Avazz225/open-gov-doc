# libs/

Geteilte Python-Pakete, die von mehreren Services genutzt werden — ausdrücklich **keine** geteilte Fachlogik, nur technische Basis (Settings, DB-Setup, Event-Bus-Client, Auth-Client). Jeder Service bleibt fachlich unabhängig (Konzept 3.1).

Wird in P0-S2 angelegt:

- `dms-common/` — Settings, Logging, OpenTelemetry-Basis
- `dms-db-base/` — SQLAlchemy-Async-Setup, Schema-pro-Service-Konvention
- `dms-eventbus-client/` — Publish/Consume-Interface über NATS JetStream (austauschbar, Konzept 3.4)
- `dms-auth-client/` — OIDC/JWT-Validierung gegen Keycloak (Konzept 4.4)
