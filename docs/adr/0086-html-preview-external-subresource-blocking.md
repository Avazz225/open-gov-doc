# 0086 — HTML preview: server-side blocking of external subresources

**Status:** accepted (Session 3 of 4, see Phase 21 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap Phase 21 Session 3, affects `document-service`, documented for `user-ui`

## Decision

`user-ui`'s `PreviewPane` renders HTML documents via a `sandbox=""` iframe with `srcDoc` (no `src`
pointing to its own origin). `sandbox=""` blocks script execution/forms/top-level navigation, but NOT
the normal loading of subresources (images, stylesheets, ...) — an uploaded HTML document with, e.g.,
`<img src="https://tracker.example/pixel.gif?...">` triggers this request merely by opening the preview,
regardless of the sandbox attribute (tracking/data-leak risk). A `Content-Security-Policy` header would
be the obvious fix, but it only has limited effect on `srcDoc` content without its own origin (no fixed
origin a header could reliably bind to — depends on browser/engine).

`document-service` therefore neutralizes external `src`/`href` references **server-side, at the source**,
rather than at render time: `GET /documents/{id}/content` and `GET /documents/{id}/versions/{n}/content`
check the (already determined via magic-byte sniffing, not trusted from the client) `content_type` — for
`"text/html"`, the content passes through a new function, `html_preview_guard.rewrite_external_references`,
before being served.

1. **Parsing with `BeautifulSoup`/`html.parser`** (new dependency) — removes any `src`/`href` reference
   that isn't `data:`/`mailto:`/`tel:` or a pure fragment anchor (`#...`). Attribute-driven rather than
   tag-name-driven (no hardcoded `img`/`script`/`iframe`/... set) — automatically also catches unusual
   tags with `src`/`href`.
2. **Relative paths are also blocked**, not just absolute URLs — `srcDoc` content has no safe base URL
   resolvable within the preview context (depending on browser implementation, relative/scheme-relative
   paths could resolve unpredictably to the parent page or unexpected targets).
3. **Visible marker instead of silent removal** — immediately after every removed reference, a visible
   `<span>` reading `[Blocked external request: <original-URL>]` is inserted, per explicit plan
   requirement.
4. **`<meta charset>` is normalized to `utf-8`** — the function always returns UTF-8 bytes, regardless of
   the originally declared encoding of the uploaded document.

## Rationale

- **Why server-side rewriting instead of a CSP header** (explicit plan requirement, confirmed here): a
  `srcDoc` iframe has no addressable origin/URL of its own that a `Content-Security-Policy` header could
  reliably bind to — behavior is browser-dependent and not robust enough for this purpose. Removing the
  reference at the source (before the browser even sees it) works regardless of browser CSP quirks.
- **Why `BeautifulSoup` as a new dependency instead of a regex-based approach or bare `html.parser`
  handlers**: attribute-wise rewriting with correct serialization (quoting, self-closing tags, entity
  handling) is error-prone with raw regex on nested, potentially malformed HTML; `html.parser.HTMLParser`
  (stdlib) is a purely SAX-style event handler with no built-in tree representation/serialization —
  correct reassembly would have to be built by hand. `BeautifulSoup` with the `html.parser` backend
  needs no extra C extension (unlike `lxml`), is an extremely well-established path for exactly this task
  (parsing untrusted HTML, mutating specific attributes, re-serializing), and justifies the new dependency
  given the security-relevant correctness requirement.
- **Why attribute-driven rather than tag-driven**: a fixed tag set (`img`/`script`/`iframe`/...) would
  need to be manually maintained for every new/unusual tag with `src`/`href` (e.g. `<object data=...>`
  would be a special case anyway, but also `<portal>`, custom elements with `src`-like attributes) — the
  attribute-driven check, by contrast, is entirely independent of the concrete tag name.
- **Why relative paths are also blocked** (not just absolute external URLs): an HTML document uploaded to
  this system has no legitimate target directory with sibling files reachable via a relative path (the
  document is a single stored object, not a directory structure) — so a relative path can never point to
  real, intended content anyway, only to something unforeseen.
- **Why deliberately only `src`/`href`, not also `srcset`/`poster`/`background`/CSS `url(...)`**: explicit
  plan requirement ("external `src`/`href`") — full coverage of every conceivable subresource vector
  would be a substantially larger task (CSS parsing for `url()` in `<style>` blocks and `style`
  attributes); implemented as a deliberately incomplete but documented first hardening layer, consistent
  with this project's pattern of honestly documenting limits instead of silently feigning completeness.

## Consequences

- **New dependency** `beautifulsoup4` in `services/document-service/pyproject.toml` — the first HTML
  parsing tool in this repo (previously only `lxml` for XML/BPMN in other services, no precedent for
  HTML).
- **Only affects `document-service`'s `GET /documents/{id}/content`/`.../versions/{n}/content`** — the
  separate `GET /public/share-links/content` (public share-link download) doesn't render inline (a plain
  `<a href download>` browser download in `user-ui`'s `SharePage`, no `srcDoc` iframe), so it is
  deliberately out of scope for this session.
- **Non-HTML content stays byte-identical** — the rewrite only applies when `content_type == "text/html"`
  (magic-byte-sniffing result, not the unverified client header).
- **Tests**: `document-service` 247 (previously 234, +13) — new test file `test_html_preview_guard.py`
  (11 pure function tests: external HTTPS/protocol-relative/relative references blocked, `data:`/
  `mailto:`/`tel:`/fragment anchors allowed, `javascript:` URIs blocked, empty `src` blocked,
  `<meta charset>` normalization, unchanged content with no references), plus 2 new `test_api.py` tests
  (end-to-end across both download endpoints including the marker text, byte identity for non-HTML
  content with randomly HTML-like text as a regression guard).
- Docs: `docs/services/document-service.md` (new "HTML Preview Hardening" section, API table),
  `docs/services/user-ui.md` ("Open Points" marked resolved).
