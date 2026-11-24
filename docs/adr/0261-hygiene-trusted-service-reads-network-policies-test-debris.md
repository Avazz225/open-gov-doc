# 0261 — Trusted service reads, NetworkPolicies, and test debris

**Status:** accepted
**Context:** P94-S3, the hygiene bundle. Most of its items were mechanical. Four of them changed security
boundaries or operating defaults, and they are recorded here.

## 1. document-service trusts a service reading as itself

**Problem:** search-service's `document.created` handler got `403` for documents in teamspace folders.
- A teamspace folder doesn't inherit the "everyone" grant, and search-service isn't a member.
- The handler raised, and JetStream redelivered the message every 30 s forever. 16 messages were stuck
  in the dev stack (ADR 0251).
- Any background reader could be locked out by a narrowed resource and fail this way.

**Decision (user, 2026-11-24):** a service principal (`dms_common.SERVICE_PRINCIPALS`) reading as
itself passes every `document.read` check in document-service. This covers
`_require_document_permission` and the three batch filters.
- **"As itself"** means no `X-DMS-On-Behalf-Of` header; `_OnBehalfOfMiddleware` keeps it per request.
- **Not covered:**
  - writes, which are always checked;
  - a service acting for a user, which is checked as before.
- **Why it is safe:**
  - The gateway strips client `X-DMS-*` headers and sets the principal from the token (a UUID or a
    numeric technical-account id). A user can't claim a service name.
  - The WebDAV and CMIS connectors pass the user's own principal, not their service name.
  - Services that serve users from what they read (search, rendering) check the caller themselves.
- **Additionally, search-service acknowledges a `403`** (logged as `search_reindex_refused`, counted
  in `search.index.failures`) instead of letting it be redelivered.
- **Live afterwards:** 0 errors, 0 redelivered messages.

## 2. A rate-limit exemption for the dev stack's e2e account

**Problem:** the user-ui, reviewer-ui and admin-ui e2e suites all log in as `users-admin`. Run back to
back they exceeded the gateway's 600 requests/min.
- Specs failed at random.
- Their cleanup calls were refused with `429`, which left a role assignment, a trust anchor and folders
  behind (P93-S3).

**Decision:**
- The gateway has `DMS_RATE_LIMIT_EXEMPT_PRINCIPALS`, a comma-separated list of verified token `sub`s.
  It is empty by default, so Helm and production are unchanged.
- Only a principal set after the token check can match; client headers are stripped.
- Compose exempts `2` (users-admin); setting it empty in `.env` tests the limit with that account.
- **Live:** all three suites back to back, 44/44 green, no `429`.

## 3. NetworkPolicies in the Helm chart, off by default

**Problem:** without NetworkPolicies every pod can reach every other pod and the internet (found in
P90-S6).

**Decision:** `networkPolicy.enabled` (default `false`).
- **Why off by default:** the cluster's CNI must enforce NetworkPolicy, and a deny-by-default rollout
  on a running installation should be a deliberate step.
- **When on:**
  - Every pod of the release denies all ingress and egress, except traffic inside the release and DNS.
  - The ingress controller may reach the published services (`ingress.enabled`) and
    `ingressController.extraComponents`.
  - Only the components in `internetEgress` may reach addresses outside the private ranges, each on
    fixed ports. These are signature-service (OCSP/CRL, EU trusted lists), mail-connector (IMAP/SMTP),
    notification-service (mail, webhooks), auth-service and gateway (an external OIDC issuer),
    workflow- and federation-hub-service (federation), fleet-management-service, and storage-service
    (external object storage).
  - The Kubernetes API needs its own rule (`kubernetesApi`): its address is private. Alloy (P94-S2)
    and plugin-orchestration-service's scheduler adapter call it.
  - `extraEgress` takes raw rules per component, e.g. an external Postgres or an SMTP relay in a
    private range.
- **Verified:** `helm lint`, `helm template` for all variants, and `kubeconform -strict` (181 resources
  valid).
- **Not verified:** no real cluster with an enforcing CNI.

## 4. Deleting what tests leave behind

**Problem:** the suites run against the live stack, and many created objects they never removed. By
this session the dev stack held:
- 1,147 root folders;
- 2,630 roles;
- 97 groups;
- 80 four-eyes configs;
- 210 Keycloak realm roles;
- 221 objects whose type a test had deleted.

Roles and realm roles couldn't be deleted at all.

**Decision:**
- **New endpoints:**
  - permission-service `DELETE /roles/{id}`. It removes the assignments and invalidates the cache.
    `permission.role.deleted` is audited. Seeded roles return `409`: every `domain-admin-*`,
    `everyone`, and the teamspace roles.
  - permission-service `DELETE /approval-config/{action_type}`.
  - auth-service `DELETE /realm-roles/{name}`. Keycloak's built-ins, `dms-admin`, `dms-poststelle` and
    `domain-admin-*` return `409`.
  - All of them sit behind the same gate as creating.
- **`scripts/cleanup-test-debris.py`:** a dry run by default, `--apply` to act.
  - It finds debris by name patterns only tests produce, each with a random suffix, or an exact name
    together with the creating test principal.
  - It finds objects of deleted types through the conformity reports.
  - Folders and documents go to the trash (restorable); everything else is deleted through the APIs.
- **`scripts/run-tests.sh` calls it after every run** (`--apply --since <run start>`, skipped with
  `--no-cleanup`). The runner thus removes what the run left, without editing ~20 test files.
  - Folders are time-bound to the run.
  - Roles, groups, configs and realm roles have no timestamp and are matched by pattern alone.
- **One-off result:** 1,043 root folders, 221 objects of deleted types, 2,585 roles, 93 groups,
  60 configs and 206 realm roles removed.
- **What remains:** 104 root folders, among them the real "Posteingang"/"Postausgang" and about 100
  test folders with their own permissions (`inherit=False`), which no admin account can see or touch by
  design.

## Also in P94-S3 (no decision needed)

- **ruff:** pinned to `0.16.0` (`uv.lock` isn't versioned).
- **SBOMs:** `scripts/sbom.sh` writes CycloneDX 1.6 per service and app (38 files); a CI job keeps them
  as an artifact.
- **Bounded shutdown:** every lifespan shutdown step in the remaining 28 services is a
  `bounded_shutdown_step`.
- **Nested sessions:** the audit found request handlers opening a second session only in mail-connector
  (7 handlers), which now pass their session down. ocr- and rendering-service commit before opening one.
- **Federation test identity:** workflow-service's persistent federation test identity carries a
  recognisable display name.
- **Docs:**
  - 16 stale open points in `docs/services` are marked closed;
  - PROGRESS.md "Open Decisions" is cut from 110 bullets to the 8 still open;
  - the historical session notes got their own heading.
