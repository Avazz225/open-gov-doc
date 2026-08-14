# 0011 — OCR: Tesseract instead of PaddleOCR as the actually wired-up default engine

**Status:** accepted
**Context:** Concept 3.9, Session P5-S3

## Decision

The new `ocr-service` implements the concept requirement "automatic
detection of whether OCR is needed at all" (3.9) via two engines behind a
shared plugin interface (`TextLayerExtractor`):

- **`NativeTextLayerEngine`**: for PDFs that already have a usable text layer
  (page 1 ≥ 20 non-blank characters), reads its words along with coordinates
  directly via PyMuPDF — no image recognition, confidence always `100.0`.
- **`TesseractEngine`**: for scanned PDFs without a usable text layer and for
  raster images directly — `pytesseract` against the `tesseract` system
  binary (`apt-get install tesseract-ocr tesseract-ocr-deu`).

**PaddleOCR, the default engine named by the concept, is not implemented** —
only the plugin interface allows for a future `PaddleOcrEngine` class,
without changing any existing code.

## Rationale

- **PaddleOCR brings in `paddlepaddle`**, a full ML framework (several
  hundred MB, GPU/CPU wheel selection, noticeable download time) - not
  suitable for a reproducible `docker compose up --build` in this
  development environment, the same basic trade-off as `ClamdEngine` vs.
  `EicarSignatureEngine` in [ADR 0010](0010-virus-scan-synchronous-gating.md).
- **Unlike ClamdEngine, there is no lightweight partial implementation
  here**: `ClamdEngine` is a thin protocol client (INSTREAM against a
  separately operated `clamd` daemon) and was still built fully, because the
  client itself stays small - only the daemon itself is the heavy component
  and is operated separately. PaddleOCR, by contrast, runs **in-process**
  within the OCR Service itself; there is no "only the client is light"
  equivalent here. Building a PaddleOCR integration would necessarily mean
  installing exactly the heavy dependency meant to be avoided. Hence:
  documented, not implemented.
- **Tesseract is already a fully-fledged alternative named in the concept
  itself** ("resource-efficient alternative") - no compromise on functional
  coverage, just a different default choice than the one suggested in the
  example text.
- **Automatic text-layer detection saves most OCR runs in practice
  anyway**: born-digital PDFs (the most common document type in a DMS
  context) go through `NativeTextLayerEngine`, not Tesseract - the choice of
  image OCR engine only affects the smaller share of actually
  scanned/image-based documents.
- **Thresholds deliberately kept simple**: "usable text layer" from 20
  non-blank characters on page 1 (filters out blank/decorative cover pages
  without misclassifying short but genuine pages) and `needs_review` below
  `average_confidence < 70.0` (a common Tesseract community heuristic) are
  deliberately rough but defensible reference values - no calibrated study,
  both easily adjustable via `Settings`.

## Consequences

- Multilingual/multi-column/tabular layouts, where PaddleOCR is said by the
  concept to be more robust, are recognized by Tesseract with lower
  accuracy - accepted, since text-layer detection does not affect the
  regular PDF case anyway, and Tesseract is sufficient for the remaining
  image-OCR share.
- A switch to PaddleOCR (or a third engine) remains possible at any time
  without changing `pipeline.py`/`select_engine()` - just registering
  another `TextLayerExtractor` implementation.
- `tesseract-ocr`/`tesseract-ocr-deu` must be present as a system package in
  the Docker image (see `services/ocr-service/Dockerfile`) - in this local
  development environment (outside the container), no `tesseract` binary is
  installed; the corresponding tests are marked with `pytest.mark.skipif`
  and are instead verified via live E2E in the real container (see
  `docs/services/ocr-service.md`).
