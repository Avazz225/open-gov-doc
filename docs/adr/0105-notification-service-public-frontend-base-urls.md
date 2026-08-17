# 0105 — Public frontend base URLs in notification-service, mirroring DMS_KEYCLOAK_PUBLIC_BASE_URL

**Status:** accepted (P27-S1, see `IMPLEMENTATION_PLAN.md`)
**Context:** new post-roadmap features (direct links, Phase 29; configurable email templates, Phase 30), affects `services/notification-service/`, precedent in `services/auth-service/` ([ADR 0062](0062-sso-automatischer-login-oidc-redirect-und-optionales-kerberos.md))

## Decision

`notification-service` gets three new, nullable settings —
`user_ui_public_base_url`, `reviewer_ui_public_base_url`,
`admin_ui_public_base_url` (`DMS_USER_UI_PUBLIC_BASE_URL` etc.) — the
browser-reachable base URL of each frontend app, so a notification email can
embed a clickable link straight to a resource (a document, a folder, a
running process instance). A new `notification_service/links.py` module
(`build_resource_link(base_url, resource_type, resource_id)`) builds the
actual URL per Phase 29's query-param scheme (`?document=`/`?folder=`/
`?instance=`); it returns `None` when the relevant base URL isn't
configured, so link-building is simply skipped rather than emitting a
broken URL.

This mirrors `auth-service`'s existing `keycloak_public_base_url`
(ADR 0062): `notification-service` only ever talks to other services over
the internal Compose/k8s network, which knows nothing about the
host-mapped ports or public ingress hosts a user's actual browser needs —
the same internal-vs-external-hostname gap Keycloak's redirect flow already
had to solve.

## Rationale

- **Three separate settings instead of one generic map**: the project has
  exactly 3 relevant frontend targets for this feature (user-ui for
  documents/folders, reviewer-ui for process instances, admin-ui reserved
  for a possible future case-file target) — a generic `dict[str, str]`
  setting would need its own validation/parsing layer for a fixed, small,
  known set of keys. Matches the plain-field style already used throughout
  `BaseServiceSettings` subclasses in this project rather than introducing
  a new settings shape.
- **`None` default, not a `localhost` default baked into the Python code**:
  unlike `docker-compose.yml`'s own `${VAR:-http://localhost:PORT}`
  fallback (dev convenience only), the Settings class itself defaults to
  `None` — a production installation that never configures these fields
  gets no links in emails rather than emails silently pointing at
  `localhost`, which would be actively wrong (and misleading) in any real
  deployment.
- **Placeholder `build_resource_link()` now, real wiring in P29-S5**: the
  Phase 29 URL scheme was already fixed at plan time (`?document=`/
  `?folder=`/`?instance=` on each app's single route), so the function
  is implemented for real already rather than as a true stub — but no
  `consumer.py` handler calls it yet (that's P29-S5, once an actual
  resource ID is available at each call site) and Phase 30's `{link}`
  template placeholder needs it too (Phase 30).

## Consequences

- An installation that wants working links in notification emails must
  set the three new env vars — not automatic, no service discovery
  attempted (consistent with `DMS_KEYCLOAK_PUBLIC_BASE_URL`'s existing
  precedent of a manually configured, browser-facing hostname).
- `docker-compose.yml`'s dev-stack defaults point at the same
  `localhost:3000`/`3005`/`3001` ports the 3 relevant apps already publish
  (`USER_UI_PORT`/`REVIEWER_UI_PORT`/`ADMIN_UI_PORT`), so links work
  out of the box in local development without extra configuration.
- `office-addin`/`process-designer`/`migration-console` deliberately have
  no corresponding setting — no notification use case links into those
  apps as of this session; a future one would add a fourth field
  following the same pattern, not a redesign.
