# 0208 — Standardized e-government transport protocol (OSCI-Transport) as a Federation Hub alternative: scoping

**Status:** accepted
**Context:** P72-S3 (Phase 72, a **scoping-only** session, explicitly no implementation commitment — see
`IMPLEMENTATION_PLAN.md` "Phase 72", last session of the phase). The plan's own framing: a standardized
secure e-government transport protocol as an alternative Federation Hub channel, for exchanging
documents with external authorities/courts that only speak that standard rather than this project's own
bespoke hub protocol — likely shape a new connector service analogous to CMIS/WebDAV, not a Federation
Hub rewrite. In German e-government, the concrete standard this describes is **OSCI-Transport** (the
protocol underlying EGVP/the justice-portal ecosystem, a double-envelope XML/SOAP scheme mediated by a
trusted "Intermediär" relay).

## Recommendation: do not build this. Two independent reasons, not one.

Unlike this phase's other two scoping sessions, this one's honest conclusion is a documented **no**, not
a bounded build plan — verified against both the real code and the real tooling landscape before
reaching that conclusion, not assumed.

### Reason 1: the Federation Hub already plays the role a second relay would play

`federation-hub-service`'s existing trust model (ADR 0028/ADR 0039/ADR 0085) is, structurally, the same
shape OSCI-Transport itself uses: each participant holds its own asymmetric keypair, payloads are
end-to-end encrypted between the two real endpoints, and a mediating relay (the hub / OSCI's
"Intermediär") forwards opaque, unreadable blobs without ever holding a plaintext copy — the hub
persists nothing of the payload itself (confirmed in `main.py`). Every prior ADR that has touched this
project's transport-layer choices (0028, 0039, 0085) has explicitly and repeatedly rejected adding real
transport-layer mTLS in favor of this exact "application-layer trust, opaque relay" shape.

Building a SECOND relay-based protocol alongside the first one is not additive the way the CMIS/WebDAV
connectors are additive (those translate a different PROTOCOL onto the SAME underlying document/folder
model). An OSCI channel would need to solve trust, encryption, and relay-mediated delivery a second time,
for a disjoint set of counterparties, using a completely different cryptographic envelope shape than the
hub already implements — this is parallel infrastructure, not a thin translation shim over existing
infrastructure.

### Reason 2: OSCI-Transport has no realistic path in this project's all-Python stack

OSCI-Transport tooling is, as far as this session could determine, essentially a Java-only ecosystem —
the reference implementation ("OSCI-Transport-Library" from the Governikus/ODM lineage) and every
production EGVP/justice-portal client built on it are Java. No maintained Python implementation of the
OSCI double-envelope (SOAP + XML-DSig + XML-Enc, a considerably more involved cryptographic envelope
than this project's own hand-rolled RSA-hybrid scheme for the Federation Hub) was found. Building this
in Python would mean either:

- **Hand-implementing the OSCI envelope from the spec** — a materially larger undertaking than
  `signature-service`'s own hand-rolled PAdES/XAdES-adjacent work (itself already this project's most
  cryptographically involved service), for a much less Python-tooled standard, with correspondingly
  higher risk of a subtly non-interoperable implementation that fails against real OSCI counterparties
  in ways that are hard to detect from this project's own test suite alone; or
- **Shelling out to a JVM sidecar** running the real Java reference library — a genuine architectural
  departure for an otherwise entirely Python/TypeScript stack, introducing a new runtime, deployment
  unit, and operational surface for exactly one connector.

Neither option is proportionate to a "reference implementation extending an existing pattern" — the
judgment this project has consistently applied to every other scoping session in this and prior rounds
(ADR 0161's Graph/O365 backend, ADR 0160's teamspace group invitation).

## What would change this recommendation

Two things, either of which would be worth a fresh scoping pass if they ever materialize:

1. **A concrete operator need** — an actual installation that must interoperate with a real German
   justice/authority OSCI endpoint, not a hypothetical future requirement. Nothing in this project's
   history (checked `docs/services/federation-hub-service.md`'s Open Points, `IMPLEMENTATION_PLAN.md`)
   names a real trigger for this beyond the generic phrasing this session itself scoped.
2. **A maintained Python OSCI library appearing** — this session's finding that none exists is a
   point-in-time fact, not a permanent one; worth re-checking before assuming the JVM-sidecar path is
   still the only option, should this ever become a real requirement.

## Consequences

- **No code diff in this session** — `federation-hub-service` and the connector services are all
  unchanged. `PROGRESS.md` marks this session explicitly as scoping-only, no feature, and explicitly
  records a "do not build" conclusion rather than a deferred-but-eventually-buildable one.
- **This concludes Phase 72** ("Scoping-Only Sessions for Larger Candidates") — `graphify update .` runs
  once at phase end per the standing rule, already covered by this same session's own commit.
- Unlike ADR 0161/ADR 0207 (this phase's other two scoping sessions, both of which hand a future build
  session a bounded starting point), this ADR's deliverable is the negative finding itself, plus the two
  named conditions above that would warrant revisiting it — consistent with this project's "no new ADR
  expected" default *not* applying here, since declining to build after a real investigation is itself
  the kind of decision this project's own discipline says to record.
