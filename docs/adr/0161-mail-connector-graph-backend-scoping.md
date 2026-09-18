# 0161 — Microsoft Graph/O365 mailbox backend for `mail-connector`: scoping

**Status:** accepted
**Context:** P43-S3 (Post-Roadmap Phase 43, concludes the phase) — a **scoping-only** session, explicitly
no implementation commitment (see `IMPLEMENTATION_PLAN.md` "Phase 43"). Follows up on the corrected gap
framing (`IMPLEMENTATION_PLAN.md`/`PROGRESS.md`): the previously suspected "IMAP is only mocked" gap
does not exist (a real, production-grade `ImapBackend`, live-verified against `greenmail`, already
exists — only the unit test suite mocks at the `imaplib` boundary, ADR 0095, because `mailpit` has no
IMAP server). What genuinely remains unbuilt is a Microsoft Graph/O365 backend for organizations on
Exchange Online, which this session scopes.

## Decision

**Recommend building a `GraphBackend` as a fourth `MailboxBackend` implementation inside the existing
`mail-connector` service — not a new service, not a change to the existing interface.** ADR 0095 already
anticipated exactly this ("a new protocol only implements `MailboxBackend`, the rest of the service
remains unchanged") and that anticipation holds up under this session's own scoping: `MailboxBackend`'s
single abstract method, `fetch_new_messages() -> list[RawIncomingMessage]`, is small enough that Graph
slots into it without any interface change.

Concrete recommendations for the future build session:

1. **Auth: OAuth2 client-credentials flow with application permissions** (`Mail.Read`, Azure AD app
   registration, tenant-admin-consented once), not delegated/per-user permissions — the correct choice
   for a headless service polling a shared/departmental mailbox with no signed-in user present. An
   Application Access Policy on the Azure AD side should scope the app to only the intended mailbox(es),
   not tenant-wide mail access — an operational/runbook concern outside this codebase, but worth naming
   explicitly so a future deployment doesn't skip it.
2. **Fetch raw MIME via Graph's `$value` endpoint, not JSON reconstruction.** `GET /users/{id}/messages/
   {id}/$value` (or `/me/...` for a delegated context, not used here) returns a message's actual raw
   MIME/RFC-822 bytes directly — feeding that straight into the existing `RawIncomingMessage.raw_bytes`
   means `main.py`'s already-existing `email`-stdlib parsing pipeline (`_parse_message`, attachment
   extraction, matching, virus scan, staging) needs **zero changes**. This resolves the one real
   interface-boundary question this scoping session set out to answer: Graph's JSON-shaped API does NOT
   require a second parse path or any change to `RawIncomingMessage`, because the raw-MIME endpoint
   exists and sidesteps the JSON/MIME mismatch entirely.
3. **Change detection: `$filter=receivedDateTime gt <last_poll_timestamp>` polling, not delta queries.**
   Graph's natural "what changed" primitive is a delta query with a persisted delta token, but that would
   require a new column/concept this project's schema doesn't have anywhere yet. A plain
   `receivedDateTime`-filtered poll on the existing `poll_interval_seconds` tick, deduplicated via
   `source_uid` (Graph's message `id` is a stable string, maps cleanly, no `UIDVALIDITY`-style epoch
   problem IMAP has), stays consistent with the existing POP3/IMAP "fetch everything new since last look,
   dedupe on our side" idiom — a deliberate simplification, not a missing feature, for a reference
   implementation.
4. **Token handling stays fully encapsulated inside the new backend** — a small in-memory cache
   (acquire a client-credentials token, reuse until near-expiry, then silently re-acquire) owned by
   `GraphBackend` itself, not a change to the poll-loop idiom or the `MailboxBackend` interface. The
   ~60-90 minute token lifetime comfortably outlives the existing `poll_interval_seconds` (default 20s)
   tick, so this is pure internal backend state, exactly the same encapsulation `ImapBackend` already
   has for its own connection lifecycle.
5. **Config: extend `MailboxConfig.inbound_protocol` with a new `"graph"` literal**, plus
   `graph_tenant_id`/`graph_client_id`/`graph_client_secret`/`graph_mailbox_address` fields on the same
   model, validated by the same per-protocol `model_validator` pattern already used for `imap_*`/`pop3_*`
   fields. **Recommend a client secret over a certificate credential** for this reference
   implementation (simpler, no certificate-management infrastructure to build) — explicitly naming the
   tradeoff Microsoft itself documents (certificates are the longer-lived, more auditable option) as a
   deliberate, reference-implementation-appropriate choice, not an oversight, mirroring this project's
   own established pattern of picking the simpler credential option and documenting the tradeoff (e.g.
   `archival-service`'s single static `EnvKeyStore` key instead of full rotation/multi-tenant key
   management).
6. **Throttling: reuse the existing `libs/dms-retry` full-jitter backoff idiom** (already used by
   `federation-hub-service`'s handover retry, `rendering-service`'s rendition retry) for Graph's `429`/
   `Retry-After` responses — no new retry mechanism needed, a direct application of an already-proven
   library.
7. **No webhook/change-notification support** — Graph supports push subscriptions for near-real-time
   mail notification, but that would need a publicly reachable HTTPS callback endpoint and a renewal poll
   loop of its own for the subscription lease, a materially larger undertaking than this reference
   implementation's existing "poll on a fixed interval" model justifies. Explicitly out of scope, named
   here so it isn't silently reinvented as a requirement by a future session without this context.

## Rationale

- **Why a fourth backend, not a new service**: the entire value of `MailboxBackend`'s narrow interface
  (already proven across POP3/IMAP) is that a new protocol is a self-contained implementation detail,
  not a service boundary change — reusing that design exactly as ADR 0095 already anticipated confirms
  the interface was designed well enough to not need revisiting three protocols later.
- **Why the `$value` raw-MIME endpoint is the load-bearing finding of this scoping session**: without
  it, the natural-seeming path would be "convert Graph's JSON message shape into something the existing
  `email`-stdlib pipeline can parse" — a real, nontrivial reconstruction problem (headers, MIME
  boundaries, attachment encoding) that would have forced either a second parse path in `main.py` or
  meaningful surgery on `RawIncomingMessage`. Confirming that Graph already exposes the actual raw bytes
  directly removes that problem entirely — this is exactly the kind of premise a build session should
  not discover the hard way mid-implementation, which is why finding it now, in a scoping pass, has real
  value (consistent with this phase's own established pattern of verifying premises before committing to
  an approach — see ADR 0159's XDOMEA-export verification and this phase's two prior corrected-premise
  sessions, P42-S1/P42-S3).
- **Why application permissions over delegated permissions**: delegated permissions need a signed-in
  user's consent and refresh token, which does not exist for an unattended background poller with no
  human present at 3am — application permissions with client-credentials are the standard, documented
  pattern for exactly this "service polls a mailbox with nobody watching" shape, and Microsoft's own
  Graph documentation treats it as the expected choice for headless mail integrations.
- **Why polling over delta queries/webhooks**: both would be more "correct" in an absolute sense
  (delta queries are more efficient than repeated `$filter` scans; webhooks are lower-latency than
  polling), but both need new persisted state (a delta token; a subscription lease) this project's schema
  has never needed before, for a service that already polls on a fixed interval everywhere else. Matching
  the EXISTING idiom is the right level of ambition for a reference implementation extending an existing
  pattern, not introducing a second one alongside it.
- **Why this is flagged as a genuinely new kind of engineering for this codebase, not a routine
  extension**: confirmed by checking every existing external-trust mechanism in the project — Keycloak
  bearer tokens are internal/user-facing, `federation-hub-service`'s trust model is RSA request-signing
  (explicitly NOT bearer/OAuth2, ADR 0028/0039), and `fleet-management-service`'s agent key is a static
  shared secret. Nothing today acquires, caches, or refreshes a token issued by an external, third-party
  identity provider. A build session should budget real time for this even though the CONCEPTUAL mapping
  onto the existing `MailboxBackend` interface is clean — the novelty is in a small, self-contained piece
  (the token client/cache), not in the rest of the integration.
- **Why a client secret, explicitly, over a certificate**: this project has repeatedly chosen the
  simpler credential shape for a reference implementation while documenting the production tradeoff
  rather than building the more complex option nobody has asked for yet (the same judgment call already
  made for `archival-service`'s encryption key). A future session or a real deployment can add
  certificate support later without revisiting this scoping's other conclusions.

## Consequences

- **A future build session inherits a fully bounded starting point**: auth model decided (client
  credentials, application permissions), the interface-boundary risk resolved (raw-MIME `$value`
  endpoint, no `RawIncomingMessage`/parsing changes needed), change-detection mechanism decided (polling
  with `$filter`, not delta/webhooks), credential shape decided (secret, not certificate), and the one
  genuinely novel piece of engineering (external OAuth2 client-credentials token acquisition/caching)
  named explicitly rather than discovered mid-implementation.
- **No code diff in this session** — `mail-connector` is unchanged. `PROGRESS.md` marks this session
  explicitly as scoping-only, no feature, per Phase 43's own Definition of Done.
- **This concludes Phase 43** ("Build/Scoping Sessions for Larger Topics") — `graphify update .` now
  runs, per the standing "only at phase-end" rule.
- **The recommended Graph backend itself remains scoped, not scheduled** — no session number assigned,
  awaiting a future phase if an installation actually needs Exchange Online mailbox support (this
  project's own IMAP/POP3 backends already cover any mailbox that still permits basic auth/IMAP, which
  remains the majority case outside organizations that have specifically disabled it in favor of modern
  auth).
