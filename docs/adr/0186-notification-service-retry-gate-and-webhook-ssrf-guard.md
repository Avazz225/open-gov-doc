# 0186 — notification-service: gate `POST /notifications/{id}/retry`, validate webhook targets

**Status:** accepted
**Context:** P61-S1 (Phase 61, "Medium-Severity Findings" — first session of the sixth gap-analysis
round's live-code security sweep). Two independent findings on `notification-service`, bundled since both
are small fixes following already-proven patterns elsewhere in this project.

**(a)** `POST /notifications/{id}/retry` had no authorization check at all — any caller could force a
manual retry of any `failed_permanent` notification by ID, and its distinct `404`/`409`/`200` responses
doubled as an ID/status enumeration oracle even after P59-S1 (ADR 0178) closed the `GET` side.

**(b)** `channel="webhook"` in `POST /notifications` sends an outbound POST to `recipient` with zero
validation (`AuthServiceClient.recipient_exists` returns `True` unconditionally for the webhook channel,
`delivery.send_webhook` uses `recipient` as-is as the target URL). `notification.write` is intended for a
narrow automated caller (`reporting-service`'s scheduler), not a general trust boundary, so any holder
could target arbitrary internal addresses.

## Decision

- **`POST /notifications/{id}/retry`** now requires `admin.notification_read` — the SAME capability as
  `GET /notifications`/`GET /notifications/{id}` (P59-S1), not a new, dedicated one. `admin-ui`'s
  `ProcessingFailuresView` is the one real caller of all three endpoints; the same admin actor who can
  already see a `failed_permanent` entry is the one who should be able to retry it. Existence (`404`) is
  checked before the permission gate, same ordering convention as everywhere else in this project.
- **`_validate_webhook_url`** (`main.py`), called only for `channel="webhook"` at notification-creation
  time. Resolves the hostname and rejects loopback/private/link-local/reserved/multicast/unspecified
  targets, **and also rejects an unresolvable hostname outright** (unlike `federation_hub_service.main.
  _validate_callback_base_url`'s more permissive design, ADR 0183) — a real webhook target should be a
  genuine, resolvable endpoint; this project has no established test convention that needs unresolvable
  webhook hosts the way federation-hub-service's peer-installation tests do. Matches
  `migration_service.main._validate_peer_base_url`'s stricter design instead (ADR 0182).
- **`settings.allow_loopback_webhooks`** (default `False`) exempts ONLY loopback — this project's own test
  suite uses `http://127.0.0.1:1/nope` as a "syntactically valid, guaranteed unreachable" webhook target
  across several existing tests (same convention `migration-service`'s `allow_loopback_peers` was built
  for). Set `true` in `infra/docker-compose.yml` for the real running container AND, separately, via
  `os.environ["DMS_ALLOW_LOOPBACK_WEBHOOKS"]` in `tests/conftest.py` — this service's test suite uses an
  in-process `TestClient(app)` (unlike `migration-service`'s real-container-HTTP suite), so its
  module-level `settings = Settings()` is instantiated by the LOCAL pytest process at import time,
  reading the local environment, never the container's — `docker-compose.yml`'s own env var addition
  alone has no effect on this particular test suite's own settings instance, a real gotcha caught by
  actually running the tests rather than assuming the same fix shape as migration-service's would transfer
  unchanged.

## Rationale

- **Why `admin.notification_read`, not a new capability**: introducing a fourth notification-service
  capability for just this one action would be over-engineering when the existing one already scopes
  correctly to the one real actor (the same admin tool, confirmed via `apps/admin-ui/src/components/
  ProcessingFailuresView.tsx`'s own `retryNotification` call).
- **Why stricter (reject unresolvable) than federation-hub-service's guard**: the two services' real
  callers and test conventions differ materially, same reasoning already established across the three
  SSRF guards built this round (`migration-service`, `federation-hub-service`, this one) — match the
  guard's permissiveness to what the REAL caller and test suite actually need, not a one-size-fits-all
  copy of whichever guard was built first.

## Consequences

- New endpoints/behavior: `POST /notifications/{id}/retry` now `401`/`403`s per the new gate.
  `POST /notifications` now `422`s for a `channel="webhook"` `recipient` resolving to (or literally being)
  a private/internal address.
- New tests: `notification-service` +4 (`test_retry_without_principal_header_is_401`,
  `test_retry_without_read_permission_is_403`, `test_create_webhook_notification_rejects_private_ip_target`,
  `test_create_webhook_notification_rejects_metadata_ip_target`), 100/100 total. `ruff` clean (same
  pre-existing, unrelated repo-wide failures confirmed out of scope again).
- Rebuilt/redeployed. **Live-verified against the real running stack**: `curl` confirmed `404` for an
  unknown notification ID's retry (existence-first), `401`/`403` for a real notification's retry without/
  with the wrong identity, and `422` for both a private and a metadata-range webhook target; a real
  loopback webhook notification still creates and records its (genuine) delivery failure correctly, since
  the real container's own `DMS_ALLOW_LOOPBACK_WEBHOOKS=true` matches the dev/test convention.
