# 0155 — Internal TSA + CRL, self-contained PAdES-B-LTA (no `archival-service` dependency)

**Status:** accepted (P41-S1, see Phase 41 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 41 Session 1 (Compliance gaps from the concept document), affects `signature-service`

## Decision

Concept 3.10 explicitly requires the PAdES-B-LTA profile for the disposal use case (5.6). Research at
session start against the actual code (not just docs) found three things:

1. **A real, silent bug**: `InternalSelfSignedConnector.sign()` never set `subfilter=SigSeedSubFilter.
   PADES`. Without it, pyHanko defaults to `ADOBE_PKCS7_DETACHED` (plain PKCS#7) — every signature this
   service ever produced was, structurally, **not actually a PAdES signature at all**, despite the
   connector's own name/docstring and this service's docs claiming "PAdES-B-B".
2. **No timestamp or revocation infrastructure of any kind exists** — no TSA, no CRL/OCSP. Both are
   structurally required by B-T (timestamp) and B-LT/B-LTA (embedded validation info: cert chain + CRL).
3. **pyHanko (already a pinned dependency, no new library needed) fully supports B-T/B-LT/B-LTA already**
   — `PdfSignatureMetadata(subfilter=, timestamper=, embed_validation_info=, validation_context=,
   use_pades_lta=)` and `signers.async_sign_pdf(..., timestamper=...)` are already-installed API surface;
   achieving B-LTA is a wiring exercise, not a new-capability build.

Three genuine architecture decisions followed from this, all made without a user question this session
(none of them is an external-facing fork — the answer follows directly from this project's own
established precedent in each case, same reasoning P40-S2/S4 used for similarly one-sided technical
corrections):

- **Internal TSA, not a real external timestamp authority**: follows the exact precedent of ADR 0025
  (`signature-service`'s own `InternalCa`) and ADR 0085 (`federation-hub-service`'s internal hub
  identity) — a real external TSA would need a business relationship this project cannot establish. A
  new, dedicated `InternalTsa` singleton (same get-or-create pattern as `InternalCa`) is issued a
  TIME_STAMPING-purpose leaf certificate FROM the existing internal root CA (`connectors.internal.
  issue_tsa_certificate`, with the Extended Key Usage extension marked critical and containing *only*
  `id-kp-timeStamping`, per RFC 3161 §2.3), and pyHanko's `DummyTimeStamper` — despite its "testing
  purposes" docstring, a genuine, complete, self-contained RFC 3161 signer — produces real `TSTInfo` CMS
  tokens from it.
- **A freshly generated, always-empty CRL for B-LT's validation info, not OCSP or a real revocation
  registry**: this internal CA design has no revocation infrastructure and none is planned (leaf
  certificates aren't meaningfully "revoked" in this design) — but PAdES-B-LT structurally requires
  revocation info alongside the certificate chain regardless. `_build_crl_der` (via `cryptography`'s
  already-available `CertificateRevocationListBuilder`) signs a new, empty CRL on every embed (initial
  `sign()` and every later `extend_timestamp_chain()` call) instead of caching one, so it is never stale
  at the moment it's embedded.
- **The periodic archive-timestamp-chain extension lives entirely inside `signature-service`, with no new
  `archival-service` dependency**: `signature-service` already has its own `DocumentServiceClient`
  (fetch + check-in), fully sufficient to self-manage re-timestamping without any new cross-service
  wiring. A new internal poll loop (`main._retimestamp_poll_loop`, same multi-phase/per-item-try-except
  idiom as every other poll loop in this project) periodically calls `InternalSelfSignedConnector.
  extend_timestamp_chain` (pyHanko's `PdfTimeStamper.async_update_archival_timestamp_chain`) on
  signatures due for renewal (`Signature.last_timestamped_at`, tracked per signature) and checks the
  extended bytes back in as a new document version. The plan's "AND closes the currently-missing doc
  cross-reference" instruction is satisfied as a pure documentation edit to `docs/services/
  archival-service.md` — no runtime relationship existed before this session and none is created now
  either, so the doc fix is honestly a doc fix, not a preview of code still to come.
- **No manual HTTP trigger for re-timestamping**: matches the established "no manual trigger" precedent
  of `document-service`'s own retention poll loop — a periodic background process, not an
  admin-invokable action.

The connector issues every signature at the full B-LTA profile unconditionally (no per-signature profile
choice, e.g. "B-B for internal drafts, B-LTA only for disposal-bound records") — 3.10 does not call for
one, and the internal TSA/CRL are already self-contained with no external cost or latency to economize on
by offering a cheaper profile.

## Rationale

- **Why fix the subfilter bug in the same session rather than filing it separately**: it is the load-bearing
  prerequisite for everything else in this session — `use_pades_lta`/`embed_validation_info` are
  meaningless without `subfilter=PADES` actually marking the signature as PAdES in the first place, and
  leaving a mislabeled "PAdES-B-B" claim in place while building B-LTA on top of it would make the
  mislabeling worse, not better.
- **Why `DummyTimeStamper` despite its name**: read directly from pyHanko's own source
  (`pyhanko/sign/timestamps/dummy_client.py`) — it is not a stub or mock; it produces a genuine, complete
  RFC 3161 `TSTInfo` token, the same shape a real external TSA would produce. The "dummy" in the name
  refers to it not making a network call to a third party, which is exactly the property this project
  wants (same reasoning as `InternalCa`).
- **Why not defer the CRL and accept a B-LT gap**: `embed_validation_info=True` requires it structurally —
  pyHanko logs a warning ("fetching is not allowed") if `allow_fetching=False` and no CRLs/OCSP responses
  are supplied, but still embeds an incomplete DSS. A signed, always-empty CRL is a five-line addition
  given `cryptography`'s existing `CertificateRevocationListBuilder`, so there was no reason to accept the
  gap.
- **Why not build a real OCSP responder instead**: no meaningfully different outcome for a self-signed
  internal CA where nothing is ever actually revoked — a CRL is simpler and sufficient for structural
  B-LT conformance.
- **Why `last_timestamped_at` on `Signature` rather than a separate tracking table**: one nullable
  timestamp column is sufficient to answer "is this signature due for its next extension" — no other
  metadata about past extensions needs to be queried or displayed anywhere in this session's scope.

## Consequences

- **New model**: `InternalTsa` (singleton, `id=1`, same get-or-create pattern as `InternalCa`).
  `Signature.last_timestamped_at` (nullable `TIMESTAMPTZ`) added via the project's usual ad-hoc
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` migration (no Alembic).
- **`InternalSelfSignedConnector` now requires four constructor arguments** instead of two
  (`ca_certificate_pem`, `ca_private_key_pem`, `tsa_certificate_pem`, `tsa_private_key_pem`) —
  `connectors.build_connector`/`build_connectors` and `main.py`'s lifespan updated accordingly.
- **New settings**: `retimestamp_poll_interval_seconds` (default 1 day) and `retimestamp_interval_days`
  (default 365) — both deliberately generous, since the actual deadline being raced against is the leaf
  certificate's own 5-year validity window, not anything tighter.
- **`SignatureOut` gained `last_timestamped_at`** (nullable) — visible via `GET /signatures`/
  `GET /signatures/{id}`, `NULL` until the first periodic extension.
- **`docs/services/archival-service.md`** gains the previously-missing cross-reference to 3.10/B-LTA,
  explicitly noting no runtime dependency exists or is planned.
- ~~**No admin UI or manual trigger** for re-timestamping in this session — purely a backend poll loop,
  same "backend before frontend" pattern used elsewhere in this project when a session's scope doesn't
  call for a UI surface.~~ — **admin-UI visibility half closed in P71-S4** (`GET /signatures/due-for-retimestamp`,
  `admin-ui`'s `RetimestampStatus.tsx`): P71-S4's own plan proposed adding both a status view AND a
  manual trigger; the trigger half was deliberately NOT built — this ADR's own "no manual trigger"
  decision above was re-verified against the current code first and found still to hold, with no new
  justification to reopen it. The read-only visibility half closes the actual gap named here.
- **Every signature produced from now on is larger** (embeds a CA chain, a CRL, and an RFC 3161
  timestamp token in addition to the content signature itself) — no functional impact, but worth noting
  for anyone reasoning about document-service storage growth.
- **Tests**: `services/signature-service/tests/test_connector_internal_pades_lta.py` (new, 5 tests) unit-
  tests the connector directly (subfilter, DSS/validation-info embedding, initial verification, and
  repeated `extend_timestamp_chain` calls staying verifiable) with no cross-service dependency.
  `services/signature-service/tests/test_retimestamp_poll_loop.py` (new, 2 tests) exercises
  `main._run_retimestamp_tick` end-to-end (real `document-service` round trip) for both the "due" and
  "not yet due" cases, built against a dedicated engine/session bound to the test's own event loop rather
  than `TestClient`'s (the latter runs the app's lifespan-bound resources, including the asyncpg engine,
  on a separate event loop internally — reusing them directly here would break under cross-loop
  asyncpg use). Existing 18 tests unaffected; suite total: 25.
