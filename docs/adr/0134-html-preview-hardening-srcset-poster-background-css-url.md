# 0134 — HTML preview hardening extended: `srcset`/`poster`/`background`/CSS `url(...)`

**Status:** accepted (P32-S5, see Phase 32+ in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 32 Session 5 (post-Phase-31 gap re-analysis, concludes Phase 32), affects
`document-service`

## Decision

`document-service`'s `html_preview_guard.rewrite_external_references` (ADR 0086) now also neutralizes
`srcset`, `poster`, `background`, and CSS `url(...)` references inside `style` attributes and `<style>`
blocks — the four gaps ADR 0086 explicitly named and deliberately left open as "a substantially larger
task" beyond that session's scope. `src`/`href`/`poster`/`background` share the exact same single-URL
check and `<span>` marker mechanism. `srcset` (a comma-separated list of "`<url> <descriptor>`" candidates)
is removed wholesale — not filtered down to only its "safe" candidates — the moment any one candidate would
be blocked. CSS `url(...)` occurrences are replaced with an empty `url()` (loads nothing) preceded by a
`/* Blockierte externe Anfrage: ... */` comment, since CSS content has no rendered-text equivalent of the
visible `<span>` marker the other attributes get.

## Rationale

- **Why `srcset` is blocked wholesale rather than filtered candidate-by-candidate**: a partially-rewritten
  `srcset` still exposes whichever candidates remain untouched, and — same reasoning ADR 0086 already gives
  for `src`/`href` — an uploaded HTML document has no legitimate external or relative image candidate to
  begin with, so there is nothing worth preserving from a mixed list.
- **`srcset` candidates are split on `,\s+` (comma followed by whitespace), not a bare comma**: a `data:`
  URI candidate's own `base64,<payload>` separator has no following whitespace (the base64 alphabet
  contains neither a comma nor a space), while the separator comma between distinct `srcset` candidates
  conventionally does. A naive bare-comma split found live during this session's own test-writing (a test
  asserting an all-`data:` `srcset` stays untouched initially failed) would otherwise cut a `data:`
  candidate's payload in half, misclassifying the second half as a schemeless (and therefore blocked)
  relative reference.
- **CSS gets a comment marker, not a `<span>`**: unlike `src`/`href`/`srcset`/`poster`/`background`, which
  sit in the rendered document flow where inserting a visible sibling `<span>` is natural, CSS content
  inside `style`/`<style>` is not itself rendered as text — a comment is the closest available analogue
  (present in the served markup for anyone inspecting source, inert to the browser, and — unlike silent
  removal — still honestly documents that something was blocked, matching this project's established
  "visible marker, not silent removal" principle from ADR 0086).
- **A single regex (`url\(...\)`, quoted/unquoted) rather than a full CSS parser**: ADR 0086 itself
  estimated "CSS parsing" as the size driver for why this was deferred — a full CSS tokenizer is
  unnecessary for the one construct that matters here (a reference-bearing function call), and a targeted
  regex keeps this a small, incremental extension of an already-accepted "first hardening layer, not full
  completeness" module rather than a new dependency or subsystem.
- **`poster`/`background` reuse the identical single-URL attribute loop as `src`/`href`** (extended
  `_URL_ATTRIBUTES` tuple) — no new logic needed, since these are structurally identical single-reference
  attributes; the attribute-driven (not tag-name-driven) design ADR 0086 chose already generalizes cleanly.

## Consequences

- **Still not exhaustive** — e.g. `<meta http-equiv="refresh" content="0;url=...">`,
  CSS `@import`, or a scripted (`fetch`/`XMLHttpRequest`) request from inline JavaScript are not covered;
  the latter is already blocked by the `sandbox=""` iframe's script-execution restriction (ADR 0086's own
  original threat model), the former two are narrower, less commonly encountered vectors left for a future
  session if ever needed — consistent with this module's established practice of documenting limits
  honestly rather than silently feigning completeness.
- **Tests**: `document-service` 349 (previously 341, +8): `test_html_preview_guard.py` gained 8 new pure
  function tests (`srcset` single/mixed/all-`data:` cases, `poster`, `background`, CSS `url()` in a `style`
  attribute/`<style>` block, `data:` CSS `url()` left unchanged); the existing end-to-end
  `test_download_content_rewrites_external_references_in_html_documents` (`test_api.py`) extended with
  `srcset`/CSS-`url()` assertions to confirm the wiring itself still applies to the newer cases, not just
  the pure function in isolation.
- **Live-verified** against the real, rebuilt running stack: an HTML document exercising all four new cases
  (`srcset`, `poster`, `background`, `style`-attribute `url()`, `<style>`-block `url()`) was uploaded and
  downloaded, confirming every reference was correctly neutralized with its marker/comment.
- Docs: `docs/services/document-service.md`'s "HTML Preview Hardening" section updated; ADR 0086
  "Rationale"/"Consequences" bullets about the deliberately-uncovered attributes marked resolved, pointing
  here.
