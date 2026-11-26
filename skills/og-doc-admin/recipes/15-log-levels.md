# Log level for a service or one area

**Example:** "Schalt für den Workflow-Dienst mal Debug-Logs ein" / "Zu viele SQL-Logs, mach das leiser"
**Role:** domain-admin-monitoring

1. `dms logging show --service <service>`: what applies now and where it comes from.
2. Narrow it down. A whole service on DEBUG is loud and can log personal data; one logger is usually
   enough. `dms logging loggers --service <service> --filter <word>` shows the names (e.g. `repository`
   for database access, `consumer` for events, `sqlalchemy.engine` for SQL, `httpx` for outgoing calls).
3. **Plan:** scope (service, logger), level, duration. DEBUG expires after 60 min unless the admin names a
   duration (at most 24 h). Say that every replica follows within 15 s.
4. After a yes: `dms logging set DEBUG --service <service> --logger <logger> --for 30m`.
5. **Verify:** `dms logging show --service <service>` shows the level with source `service`. The log lines
   themselves are in Grafana/Loki (each instance writes `log_levels_applied` when it switches).
6. When the admin is done, offer `dms logging reset --service <service> --logger <logger>` instead of
   waiting for the expiry.

**Note:** an unknown service name is refused with the list of registered ones. Pick the right one from
it; don't guess.
