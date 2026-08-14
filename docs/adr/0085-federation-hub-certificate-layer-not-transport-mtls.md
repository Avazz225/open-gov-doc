# 0085 — federation-hub-service: certificate layer instead of real transport mTLS

**Status:** accepted (Session 2 of 4, see Phase 21 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap Phase 21 Session 2, affects `federation-hub-service`

## Decision

The plan describes this session as "mTLS / real installation identity": installations are supposed to
authenticate via client certificates instead of purely signature-based auth, with a small dedicated CA
modeled on `signature-service`'s already-existing internal CA ([ADR 0025](0025-signature-service-internal-ca-and-connector-plugin.md))
as a template. A closer review found: [ADR 0039](0039-federation-trust-hardening-request-signing-over-mtls.md)
already explicitly evaluated and rejected real transport mTLS for this exact hub once before, for reasons
that still hold unchanged — **not a single service in this repo terminates TLS itself or verifies client
certificates**, all internal calls run over plain HTTP within the Docker Compose network; an isolated
mTLS special case just for the hub (its own certificate issuance/distribution,
`ssl_cert_reqs=CERT_REQUIRED` in uvicorn, certificate mounting in `infra/docker-compose.yml`) would have
no reuse value for the rest of the system and would introduce an operational/certificate-management
discipline that doesn't otherwise exist in this project. A renewed review of the entire repo confirms:
nothing has changed on that front — no TLS termination, no reverse proxy/ingress exists anywhere in the
stack.

This session resolves that by taking the plan wording **literally at the phrase "add a certificate
layer"**, not at "mTLS": a real certificate layer is added, but it stays entirely at the application
layer, exactly like the already-existing signature verification (ADR 0039 "mTLS-equivalent at the
application layer").

1. **`HubIdentity` gains its own small root CA in addition** — the same RSA-2048 key pair the hub already
   uses for `X-Federation-Hub-Signature` is additionally wrapped as a self-signed X.509 certificate
   (`ca_certificate_pem`, new field) — NO separate key pair, purely a certificate wrapper, same
   library/convention as `signature-service.connectors.internal.generate_root_ca` (ADR 0025).
2. **Every installation gets an X.509 certificate signed by the hub, both at registration AND at every
   key rotation** (`Installation.certificate_pem`/`certificate_not_after`), binding its public key — a
   simplified CSR equivalent: the hub does not generate a new key pair, but certifies the public key the
   installation itself submitted, already proven via signature. Validity 1 year (notably shorter than
   `signature-service`'s 5 years, ADR 0025 — there a long lifetime prevents a false "expired" result on
   later verification without a timestamping service; that problem doesn't exist here, so a shorter
   lifetime instead gives the certificate layer a real, recurring renewal cadence).
3. **`authenticate_signed_request` additionally verifies, alongside the existing signature check, the
   full certificate chain up to the hub CA, the validity window, AND that the certificate's `CommonName`
   plus embedded public key actually belong to the calling installation** — deliberately ADDITIONAL, not
   a replacement for the signature check.
4. **New `GET /ca-certificate` endpoint** (counterpart to `GET /public-key`) — installations can fetch
   the root CA certificate on first contact and pin it locally (trust-on-first-use, certificate-pinning
   equivalent).
5. **Backfill migration**: all installations registered before this session (`certificate_pem IS NULL`)
   automatically get a certificate issued on the next hub startup (`main.lifespan`) —
   `authenticate_signed_request` skips the certificate check only for the brief window in which a row
   still has no certificate (grandfathering, should not occur afterward).

## Rationale

- **Why the certificate layer stays purely at the application layer instead of real transport mTLS**: see
  above — ADR 0039's rationale still holds unchanged, nothing about this project's infrastructure has
  changed since then. Real transport TLS/mTLS remains purely an operator deployment decision (reverse
  proxy/ingress), same as for any other service (Concept 10.3) — unchanged from ADR 0039's own conclusion.
- **Why the hub's own key pair is reused as the CA instead of a new, separate CA key**: the hub already
  has a trusted identity distributed via trust-on-first-use (`GET /public-key`) — a second, independent
  key pair solely for the CA role would provide no security benefit (both would need to be equally
  protected against compromise) and would only increase operational complexity (two keys to secure/rotate
  instead of one).
- **Why the certificate check verifies chain+validity+identity binding ALL FOUR together**: an early draft
  of this session checked only chain+validity, without verifying that the certificate actually belongs to
  the calling installation — while writing the tests it became apparent that this would have allowed any
  certificate validly issued by the hub (e.g. one belonging to a different, unrelated installation) to be
  substituted unnoticed, without the pure chain check catching it (the signature check with the actual
  private key remains the real proof of possession and prevents a full auth bypass, but the certificate
  layer itself would not have kept its actual promise — "this key is verified to belong to this
  installation"). Fixed before it reached the code.
- **Why `certificate_not_after` is only a denormalized display value, not an independent check**: the
  actual security check always happens from the certificate bytes themselves
  (`crypto_utils.verify_installation_certificate`), the stored date is only a convenient copy of the same
  value for the admin UI / migration detection.
- **Why `POST /installations/{id}/rotate-key` MUST issue a new certificate**: a certificate for the old
  key would remain issuable-verifiable after rotation, but would bind a key that is no longer current —
  the same category of bug as the `reset_for_retry` finding documented in ADR 0080 for this roadmap
  phase, here avoided already at design time, before live verification.

## Consequences

- **Migration of already-running installations**: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in the
  lifespan for `ca_certificate_pem` (`hub_identity`) and `certificate_pem`/`certificate_not_after`
  (`installation`), followed by a backfill issuance for all already-existing installations without a
  certificate.
- **Tests**: 55 (previously 43, +12) — hub CA is self-signed and stable across repeated calls,
  registration issues a certificate signed by the hub CA with the correct `CommonName`, a self-signed
  (not hub-issued) certificate is rejected, an expired certificate is rejected, rotation issues a new
  certificate bound to the new key, `list_installations_without_certificate` filters correctly,
  grandfathering for installations without a certificate, a certificate issued for a DIFFERENT
  installation (but validly hub-signed) is rejected due to missing identity binding, `GET /ca-certificate`
  returns a valid self-signed certificate, the registration response includes a verifiable certificate,
  rotation passes through a new certificate via the API.
- **Verified live against the actual running stack** (image rebuild + restart): the backfill migration
  ran against **219 real, already-existing installations** from earlier live verifications of this and
  previous sessions (startup duration 6.2s instead of the usual ~150ms, after which all 219 rows were
  confirmed to have `certificate_pem`); `GET /ca-certificate` returns a real, self-signed certificate; a
  freshly registered installation received a certificate that demonstrably chains to the hub CA, carries
  the correct `CommonName`, and binds the submitted public key; a subsequent key rotation issued a new
  certificate bound to the new key, still chaining to the same hub CA.
- Docs: `docs/services/federation-hub-service.md` (trust model section, API table, data model,
  "Open Points" — corrected the "No mTLS" line already flagged there as outdated, test overview).
