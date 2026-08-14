"""Server-side neutralization of external sub-resource references in
HTML preview content (post-roadmap phase 21 session 3, ADR 0086) - see
`main.py`'s `download_current_content`/`download_version_content`.

Reason: `user-ui`'s `PreviewPane` renders HTML documents via a
`sandbox=""` iframe with `srcDoc` (no `src` pointing to its own origin) -
`sandbox=""` blocks script execution/top-level navigation/forms, but NOT
the normal loading of sub-resources (images, stylesheets, ...), and a
CSP header only has limited effect on `srcDoc` content without its own
origin (no fixed origin for a header to bind to). An uploaded HTML
document with, e.g., ``<img src="https://tracker.example/pixel.gif?...">``
would otherwise trigger this request simply by opening the preview
(tracking/data-leak risk), regardless of the sandbox attribute."""

from urllib.parse import urlsplit

from bs4 import BeautifulSoup

# mailto:/tel: do not trigger a network request within the page
# even on click (they open an external handler at most), data: is already
# fully embedded in the document - none of the three is an external
# sub-resource request.
_ALLOWED_SCHEMES = {"data", "mailto", "tel"}


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


def rewrite_external_references(html_bytes: bytes) -> bytes:
    """Replaces every external `src`/`href` reference of any tag with
    nothing (attribute removed, prevents the request) and inserts a
    visible marker directly after it - `data:`/`mailto:`/`tel:` URIs and
    plain fragment anchors (``#...``) are left unchanged (no network
    access). Attribute-driven rather than tag-name-driven (no hardcoded
    ``img``/``script``/``iframe``/... set) - this automatically also
    covers unusual tags with `src`/`href`. Deliberately NOT covered (see
    ADR 0086 "Consequences"): `srcset`, `poster`, `background`, as well as
    `url(...)` within `style` attributes/`<style>` blocks - the plan
    explicitly names only `src`/`href`."""
    soup = BeautifulSoup(html_bytes, "html.parser")

    meta_charset = soup.find("meta", charset=True)
    if meta_charset is not None:
        meta_charset["charset"] = "utf-8"

    for tag in soup.find_all(True):
        for attribute in ("src", "href"):
            value = tag.get(attribute)
            if value is None or not _is_blocked(value):
                continue
            del tag[attribute]
            marker = soup.new_tag("span")
            marker["class"] = "dms-blocked-external-resource"
            marker["style"] = (
                "color:#b91c1c;background:#fee2e2;font-size:0.75rem;"
                "font-family:monospace;padding:0 0.25rem;border-radius:0.2rem;"
            )
            marker.string = f"[Blockierte externe Anfrage: {value}]"
            tag.insert_after(marker)

    return soup.encode("utf-8")
