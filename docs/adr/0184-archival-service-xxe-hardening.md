# 0184 — archival-service: harden all attacker-facing XML parsing against XXE

**Status:** accepted
**Context:** P60-S2 (Phase 60, "High-Severity Findings" — second session of the sixth gap-analysis
round's live-code security sweep). `archival-service`'s XDOMEA/XJustiz import parsers (`xdomea.py`,
`xjustiz.py`) called `etree.fromstring(xml_bytes)` at five sites on attacker-supplied XML (reached from
`POST /xdomea/import`/`POST /xjustiz/import`, gated only by `archival.write` — granted to "everyone" by
default per this service's own docstring), with no hardening on lxml's default parser — unlike
`_load_schema()` in the same files, which DOES use a hardened resolver, but only for trusted, vendored
schema files. A classic XXE (CWE-611): an uploaded ZIP containing `abgabe.xml`/`xjustiz_nachricht.xml`
with a crafted `<!DOCTYPE>` declaration gets its entities parsed the moment `etree.fromstring` runs.

## Decision

Hardened every attacker-facing call site (`xdomea.validate_message`, `xdomea.validate_abgabe_message`,
`xdomea.parse_abgabe_message`, `xjustiz.validate_uebermittlung_schriftgutobjekte`,
`xjustiz.parse_uebermittlung_schriftgutobjekte` — five sites, not the plan's originally-counted four) via
a shared `_parse_untrusted_xml(xml_bytes)` helper in each module, using
`etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)`. `_load_schema()` in both
modules is untouched — it only ever parses trusted, vendored schema files at import time, never attacker
input.

## Rationale — a finding refined by testing, not just implemented as described

Before writing the fix, verified the actual exploitability in this project's pinned lxml/libxml2 version
(6.1.1 / 2.14.6) rather than treating "lxml + `fromstring` = XXE" as self-evidently true regardless of
version — a live Python repl against the exact dependency confirmed two distinct, previously-conflated
findings:

- **The literal "classic" XXE this finding's own wording describes — a `file:// SYSTEM` entity
  exfiltrating `/etc/passwd`** — turned out to **already be unreachable by default** in this lxml/libxml2
  version. `load_dtd` defaults to `False`, and nothing in this codebase ever sets it `True`; without it, a
  `<!ENTITY xxe SYSTEM "file:///etc/passwd">` declaration is never even parsed, so `&xxe;` raises
  `XMLSyntaxError: Entity 'xxe' not defined` rather than resolving — confirmed against both the bare
  default parser and an explicit unhardened `etree.XMLParser()`, i.e. this was true of the ORIGINAL,
  unfixed code too.
- **Internal-entity expansion (a billion-laughs-style DoS, CWE-776, related but distinct from CWE-611)
  IS real and reachable by default** — an internal `<!ENTITY>` chain (no `SYSTEM`/external fetch needed)
  expands fully under the original, unhardened code (confirmed: a 4-level, 10-repetition chain alone
  produced 3000 characters from a few dozen bytes of input — a real, if shallow in this test, resource-
  amplification vector that scales combinatorially with more levels).

`resolve_entities=False` closes BOTH: it leaves entity references as unexpanded `etree.Entity` placeholder
nodes rather than substituting them, regardless of whether the entity would have been a "safe" internal
value, a same-document self-reference, or a dangerous external one — the flag doesn't discriminate between
attack classes, so the file-read vector stays closed as genuine defense-in-depth even though it happened
to already be closed by an upstream default, and the entity-expansion vector (which the upstream default
does NOT close) is the one this fix actually, demonstrably closes. `no_network=True` additionally blocks
the network-fetch variant of external entities (`http://`/`ftp://` `SYSTEM` URIs), a case the `load_dtd`
default does not cover the same way file access does. `huge_tree=False` is lxml's own existing default,
named explicitly for documentation clarity rather than changing behavior.

**Included a real regression test rather than treating the parser-flag change as self-evidently
sufficient** (per this finding's own explicit ask): a billion-laughs-style payload against
`_parse_untrusted_xml` directly, asserting the entity stays an unexpanded `etree.Entity` node
(`root.text is None`, `[child.tag for child in root] == [etree.Entity]`) rather than expanding to
`"lollollollol..."` — the genuinely demonstrable exploit in this environment, not the file-read framing
that turned out already-closed.

## Consequences

- New tests: `archival-service` +2 (`test_untrusted_xml_parser_does_not_expand_entities` in both
  `test_xdomea.py` and `test_xjustiz.py`), 147/147 total.
- `ruff` clean (same pre-existing, unrelated repo-wide failures confirmed out of scope again).
- Rebuilt/redeployed. **Live-verified against the real running stack**: uploaded a real ZIP containing a
  billion-laughs-style `abgabe.xml` through `POST /xdomea/import` against the live container — rejected
  cleanly as a structurally-invalid XDOMEA message (`422`, schema validation failure, since the payload's
  own root element is `<lolz>`, not a real XDOMEA message), no crash, no entity content anywhere in the
  response or the service's own logs. Throwaway test folder cleaned up afterward.
