# 0214 — permission-service: emergency-shutdown audit-priority marker, final decline

**Status:** accepted
**Context:** P73-S4 (Phase 73, ninth gap-analysis round). Bundled with the audit-trail-completeness
`actor` threading in this same session. Concept 4.8 asks for "highest audit priority" on
emergency-shutdown-class events — `AuditEvent` has no priority/severity field at all, so this has never
been built. This exact ask has now been examined **four times**: declined at inception (ADR 0023/0024,
P6-S5/P6-S6, no priority field designed into the audit model from the start), re-examined and declined
again at P63-S1, and re-examined and declined a third time at P71-S2 ([ADR 0206](0206-p71s2-forensic-trace-coverage-bundle.md)),
which closed with an explicit "should be treated as settled unless a genuinely new justification emerges."

## Decision

**Declined a fourth time. No code change.** This session's own independent research (part of the ninth
gap-analysis round, done without first re-reading ADR 0206's text) reached the identical conclusion
before cross-checking against it: no new trigger exists. Specifically checked and still true:

- `AuditEvent`'s schema is unchanged since ADR 0206 — no priority/severity column exists anywhere in
  `audit-service`.
- No operator, incident, or downstream consumer request for this has surfaced since P71-S2 — the same
  "no new justification found" verdict ADR 0206 already reached still holds three phases later.
- `reporting-service`'s forensic-trace categorization (`forensic.py`) still categorizes generically by
  event-type suffix, not an explicit severity allowlist — the architectural reason a priority field was
  never structurally necessary (consumers can already distinguish event *kinds*, just not an explicit
  *severity* ranking within them) is unchanged.

This ADR exists so a **future** gap-analysis round has a citable, current-round confirmation to check
before proposing this a fifth time — not because the underlying analysis changed, but because "cite the
three ADRs that already declined this" is exactly the kind of context a future round without this
session's memory would otherwise have to re-derive from scratch.

## Rationale

- **Why re-confirm rather than just re-cite ADR 0206 verbatim**: a stale citation chain that nobody
  re-verifies is how a genuinely outdated decision would eventually persist past its expiry — this
  session's job was specifically to check whether anything *had* changed, not to assume it hadn't. It
  hadn't, but checking is the point.
- **Why still no code, given the concept explicitly asks for this**: Concept 4.8's "highest audit
  priority" was written before this project's actual audit-trail design (a plain, hash-chained,
  append-only event log with no severity dimension) existed — ADR 0023/0024 already noted this same
  tension when the audit model was first built. Retrofitting a priority field now would touch
  `audit-service`'s event schema and every consumer that reads it (`reporting-service`'s forensic
  categorization, any future admin-ui severity filter) for a feature with zero identified consumer of
  that severity signal in four examination rounds — the same "don't build for a hypothetical need"
  discipline this project applies elsewhere (see `CLAUDE.md`).
- **Why this doesn't get its own strikethrough in `docs/services/permission-service.md`**: strikethrough
  in this project's convention marks something *closed by being built* — this is closed by being
  *definitively declined*, a different outcome. The Open Points bullet is extended with this round's
  confirmation instead, keeping the "still open, here's why, here's the count" framing accurate.

## Consequences

- `docs/services/permission-service.md`'s Open Points bullet on this topic extended: now cites ADR 0023,
  ADR 0024, P63-S1, ADR 0206, **and this ADR** — four examination rounds, zero changes in verdict.
- No code diff, no test change, no rebuild/redeploy needed (pure documentation/decision session, same
  shape as ADR 0208/0209's scoping-only outcomes).
- **Should a fifth round ever reopen this**, it should look for one of: a real incident where the lack of
  a severity marker measurably slowed incident response; a new consumer (e.g. an alerting integration)
  that specifically needs a severity dimension the event-type suffix can't already provide; or a broader
  audit-model redesign already underway for unrelated reasons that a priority field could ride along
  with cheaply. Absent one of those three, re-declining without new analysis is the correct outcome, not
  a symptom of the question being avoided.
