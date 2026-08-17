# 0109 — Direct-link URL scheme: query param on the existing route, permission-check resolution

**Status:** accepted (P29-S1/S2/S5, see `IMPLEMENTATION_PLAN.md`)
**Context:** new post-roadmap feature (authenticated direct links), affects `apps/user-ui/`, `apps/reviewer-ui/`, `services/notification-service/`, precedent in `services/document-service/` (share links, [ADR 0047](0047-public-share-link-query-param-token-and-disable-semantics.md))

## Decision

Documents, folders (user-ui) and process instances/"Vorgänge" (reviewer-ui)
get direct links of the shape `<app-origin>/?<kind>=<id>` —
`/?document=<id>`, `/?folder=<id>`, `/?instance=<id>` — a query parameter
on each app's already-existing single route (`page.tsx`), read directly
from `window.location.search` on mount and resolved via the
**already-existing** authenticated endpoints
(`GET /documents/{id}`/`GET /folders/{id}`/`GET /instances/{id}`) and the
**already-existing** client-side navigation functions
(`openDocumentTab`/`openFolderPath` in `DocumentWorkspace.tsx`). No new
backend endpoint, no new routing concept, no token or secret of any kind —
access control is exactly the same `permission-service` check the resource
would already require through the normal UI.

This is deliberately **structurally opposite** to the existing public share
link (ADR 0047): a share link is anonymous (no `X-DMS-Principal`,
reachable via the gateway's `public_routes` allow-list), single-document,
and time-limited via a `secrets.token_urlsafe(32)` bearer token that IS the
access grant. A direct link carries no secret at all — the resource ID in
the URL is not sensitive on its own, and the actual authorization check
happens exactly where it always does, at request time, gated by the
viewer's own session and permissions. Opening a direct link without access
gets the same `403`/`404` an in-app navigation attempt would, surfaced
visibly (not a silent fallback to the root folder).

**"Copy link"** in both `ExplorerPane.tsx`'s context menus and
`PreviewPane.tsx` builds the URL client-side
(`${window.location.origin}/?<kind>=<id>`) and calls
`navigator.clipboard.writeText()`, with a transient hint on success/failure
— the first use of the Clipboard API in this codebase (existing share-link
UI instead uses a `readonly` input with select-on-focus, ADR 0047's own
established pattern, but a context-menu action has no natural place to show
a persistent input field).

## Rationale

- **Query param on the existing route, not a new Next.js route**: both
  `user-ui` and `reviewer-ui` are single-route, `output: "export"` SPAs
  (ADR 0006) where "which resource is open" already lives entirely in React
  state, not the URL, for in-app navigation. Adding a param that state can
  initialize from on mount is a much smaller change than introducing
  client-side routing (which `output: "export"` doesn't support server-side
  anyway) or a second, parallel "detail page" concept.
- **`window.location.search` read directly, not `useSearchParams()`**:
  avoids a `<Suspense>` boundary requirement under `output: "export"` — see
  ADR 0106's identical reasoning for `RequireAuth.tsx`'s `returnTo`. The
  resolving effect in `DocumentWorkspace.tsx`/`page.tsx` (reviewer-ui) is a
  plain `useEffect` reading `window.location.search` once on mount.
- **Reusing existing resolution/navigation functions instead of new ones**:
  `openDocumentTab`/`openFolderPath` already do exactly "fetch a resource
  and put the UI in the right state" for the favorites/teamspaces flows —
  a direct link is just one more caller. `GET /instances/{id}` already
  existed in workflow-service (used internally, never surfaced in any UI
  before this feature).
- **No token, unlike the share link**: the two use cases solve different
  problems. A share link exists so an *external, unauthenticated* party can
  see one specific document without an account. A direct link exists so an
  *already-authorized* internal user (e.g. clicking a link in a
  notification email while logged in) lands where they need to be — the
  permission check is the point, not something to bypass.

## Consequences

- A direct link is only as durable as the resource and the viewer's
  ongoing permissions — unlike a share link, there is no separate
  expiry/revocation mechanism to reason about (there is nothing to revoke;
  revoking access means the normal RBAC change it always would).
- `returnTo` (ADR 0106) is required for a direct link to survive an
  unauthenticated visit gracefully: `RequireAuth.tsx` already preserves the
  full path+query (including `?document=`/`?folder=`/`?instance=`) across
  the login round trip, so a link opened while logged out still lands on
  the right resource after login, not the root folder.
- `notification-service`'s `build_resource_link()` (ADR 0105) is now wired
  into the three handlers that already had a concrete resource reference
  at hand (`_handle_task_escalated`, `_handle_deletion_reminder`,
  `_handle_folder_deletion_reminder`) — other handlers (break-glass,
  license, maintenance mode) have no single addressable DMS resource to
  link to and were left unchanged.
