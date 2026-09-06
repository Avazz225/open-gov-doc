"""Server-side neutralization of external sub-resource references in
HTML preview content (post-roadmap phase 21 session 3, ADR 0086; extended
post-roadmap phase 32 session 5, ADR 0134) - see `main.py`'s
`download_current_content`/`download_version_content`.

Reason: `user-ui`'s `PreviewPane` renders HTML documents via a
`sandbox=""` iframe with `srcDoc` (no `src` pointing to its own origin) -
`sandbox=""` blocks script execution/top-level navigation/forms, but NOT
the normal loading of sub-resources (images, stylesheets, ...), and a
CSP header only has limited effect on `srcDoc` content without its own
origin (no fixed origin for a header to bind to). An uploaded HTML
document with, e.g., ``<img src="https://tracker.example/pixel.gif?...">``
would otherwise trigger this request simply by opening the preview
(tracking/data-leak risk), regardless of the sandbox attribute."""

import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

# mailto:/tel: do not trigger a network request within the page
# even on click (they open an external handler at most), data: is already
# fully embedded in the document - none of the three is an external
# sub-resource request.
_ALLOWED_SCHEMES = {"data", "mailto", "tel"}

# Single-URL attributes checked/blocked identically to `src`/`href` (ADR
# 0086) - `poster`/`background` were deliberately left uncovered there,
# closed in ADR 0134 alongside `srcset`/CSS `url(...)`.
_URL_ATTRIBUTES = ("src", "href", "poster", "background")

_CSS_URL_PATTERN = re.compile(
    r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^'")]*?))\s*\)""",
    re.IGNORECASE,
)


def _is_blocked(value: str) -> bool:
    value = value.strip()
    if not value:
        # An empty `src`/`href` value causes some browsers to re-request
        # the current page (a known quirk) - blocked as a precaution
        # instead of being treated as harmless.
        return True
    if value.startswith("#"):
        return False
    parts = urlsplit(value)
    if parts.scheme:
        return parts.scheme.lower() not in _ALLOWED_SCHEMES
    # No scheme: either scheme-relative ("//host/...", for which
    # `urlsplit` returns an empty `scheme` with `netloc` set) or a relative
    # path - both are blocked, since `srcDoc` content has no safe base URL
    # that can be resolved in the preview context.
    return True


def _srcset_is_blocked(value: str) -> bool:
    """`srcset` (ADR 0134) packs one or more "<url> <descriptor>" candidates
    separated by commas - blocked wholesale (like `src`/`href`) the moment
    ANY candidate would be blocked individually, rather than filtering the
    list down to only the "safe" ones: a partially-rewritten `srcset` still
    exposes whichever candidates remain, and there is no legitimate
    candidate in an uploaded HTML document to begin with (same "no safe
    base URL" reasoning ADR 0086 already gives for `src`/`href`). Splits on
    a comma followed by whitespace, NOT a bare comma - a `data:` URI's own
    `base64,<payload>` comma has no following whitespace (the base64
    alphabet contains neither a comma nor a space), while the separator
    comma between candidates conventionally does; a bare-comma split would
    incorrectly cut a `data:` candidate in half."""
    candidates = [c.strip() for c in re.split(r",\s+", value) if c.strip()]
    urls = [c.split()[0] for c in candidates if c.split()]
    return any(_is_blocked(url) for url in urls)


def _rewrite_css_urls(css_text: str) -> str:
    """Replaces every blocked `url(...)` reference (CSS `style` attributes
    and `<style>` blocks, ADR 0134) with an empty `url()` (loads nothing)
    preceded by a `/* Blockierte externe Anfrage: ... */` comment - CSS has
    no rendered-content equivalent of the `<span>` marker `src`/`href`/
    `srcset`/`poster`/`background` get, a comment is the closest analogue
    (visible in the served markup, inert to the browser). `data:`/relative-
    reference semantics are identical to every other attribute here (see
    `_is_blocked`)."""

    def replace(match: re.Match[str]) -> str:
        value = match.group(1) or match.group(2) or match.group(3) or ""
        if not _is_blocked(value):
            return match.group(0)
        return f"/* Blockierte externe Anfrage: {value} */url()"

    return _CSS_URL_PATTERN.sub(replace, css_text)


def _insert_marker(soup: BeautifulSoup, tag, original_value: str) -> None:
    marker = soup.new_tag("span")
    marker["class"] = "dms-blocked-external-resource"
    marker["style"] = (
        "color:#b91c1c;background:#fee2e2;font-size:0.75rem;"
        "font-family:monospace;padding:0 0.25rem;border-radius:0.2rem;"
    )
    marker.string = f"[Blockierte externe Anfrage: {original_value}]"
    tag.insert_after(marker)


def rewrite_external_references(html_bytes: bytes) -> bytes:
    """Replaces every external `src`/`href`/`srcset`/`poster`/`background`
    reference of any tag with nothing (attribute removed, prevents the
    request) and inserts a visible marker directly after it, and neutralizes
    every external CSS `url(...)` reference in `style` attributes/`<style>`
    blocks (ADR 0134 - closes the gap ADR 0086 "Consequences" explicitly
    left open) - `data:`/`mailto:`/`tel:` URIs and plain fragment anchors
    (``#...``) are left unchanged (no network access). Attribute-driven
    rather than tag-name-driven (no hardcoded ``img``/``script``/``iframe``/
    ... set) - this automatically also covers unusual tags with these
    attributes."""
    soup = BeautifulSoup(html_bytes, "html.parser")

    meta_charset = soup.find("meta", charset=True)
    if meta_charset is not None:
        meta_charset["charset"] = "utf-8"

    for tag in soup.find_all(True):
        for attribute in _URL_ATTRIBUTES:
            value = tag.get(attribute)
            if value is None or not _is_blocked(value):
                continue
            del tag[attribute]
            _insert_marker(soup, tag, value)

        srcset_value = tag.get("srcset")
        if srcset_value is not None and _srcset_is_blocked(srcset_value):
            del tag["srcset"]
            _insert_marker(soup, tag, srcset_value)

        style_value = tag.get("style")
        if style_value:
            rewritten_style = _rewrite_css_urls(style_value)
            if rewritten_style != style_value:
                tag["style"] = rewritten_style

    for style_tag in soup.find_all("style"):
        original_css = style_tag.get_text()
        rewritten_css = _rewrite_css_urls(original_css)
        if rewritten_css != original_css:
            style_tag.string = rewritten_css

    return soup.encode("utf-8")
