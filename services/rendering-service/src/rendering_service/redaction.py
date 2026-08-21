import fitz  # PyMuPDF

# Document redaction (14.2, post-roadmap phase 31 session 4, ADR 0115).
# Deliberately PyMuPDF (`fitz`), not pypdf/reportlab (used by watermark.py):
# `page.add_redact_annot()` + `page.apply_redactions()` perform GENUINE
# content-stream removal (text/graphics intersecting the region are actually
# deleted, not just visually covered) - unlike watermark.py's
# `page.merge_page()`, which only overlays a semi-transparent stamp on top of
# unchanged underlying content. This is what lets a redacted copy's later
# OCR/native-text-layer extraction naturally omit the removed content with no
# separate "exclude from search index" mechanism needed anywhere downstream
# (see ADR 0115). `fitz` is already a proven dependency in this project
# (ocr-service, for OCR page rasterization/native-text-layer extraction).


class InvalidRedactionRegionError(Exception):
    """A region referenced a page number outside the document's actual page
    range (post-roadmap phase 31 session 4, ADR 0115)."""


def get_page_count(data: bytes) -> int:
    with fitz.open(stream=data, filetype="pdf") as doc:
        return doc.page_count


def render_page_image(data: bytes, page_number: int, *, dpi: int = 150) -> bytes:
    """1-indexed `page_number`, matching every other per-page API in this
    project (see `ocr_service.models.OcrResult.pages`). Used by the
    redaction UI to let a user see a page and draw regions on it - any PDF
    page can be rasterized this way, regardless of whether it has a native
    text layer or is a scan (unlike `ocr-service`'s page images, which only
    exist for the Tesseract-processed subset)."""
    with fitz.open(stream=data, filetype="pdf") as doc:
        if page_number < 1 or page_number > doc.page_count:
            raise InvalidRedactionRegionError(
                f"Seite {page_number} existiert nicht (Dokument hat {doc.page_count} Seite(n))"
            )
        pixmap = doc[page_number - 1].get_pixmap(dpi=dpi)
        return pixmap.tobytes("png")


def apply_redactions(data: bytes, regions: list[dict]) -> bytes:
    """`regions`: list of `{page_number, x, y, width, height}`, all of
    `x`/`y`/`width`/`height` fractions (0..1) of that page's own dimensions -
    resolution-independent, same convention as the frontend's existing
    OCR-word-overlay positioning (`PreviewPane.tsx`, percentages of the page
    image). Fractions are converted to PDF-point rectangles per page (PDF
    origin is bottom-left, but `fitz.Rect` and `get_pixmap()` both use a
    top-left, y-grows-downward convention consistently, so no axis mirroring
    is needed here - verified against `ocr_service.text_layer.embed_text_layer`,
    which relies on the same top-left convention for OCR word placement)."""
    with fitz.open(stream=data, filetype="pdf") as doc:
        touched_pages: set[int] = set()
        for region in regions:
            page_number = region["page_number"]
            if page_number < 1 or page_number > doc.page_count:
                raise InvalidRedactionRegionError(
                    f"Seite {page_number} existiert nicht (Dokument hat {doc.page_count} Seite(n))"
                )
            page = doc[page_number - 1]
            rect = fitz.Rect(
                region["x"] * page.rect.width,
                region["y"] * page.rect.height,
                (region["x"] + region["width"]) * page.rect.width,
                (region["y"] + region["height"]) * page.rect.height,
            )
            page.add_redact_annot(rect, fill=(0, 0, 0))
            touched_pages.add(page_number - 1)
        for page_index in touched_pages:
            doc[page_index].apply_redactions()
        return doc.tobytes()
