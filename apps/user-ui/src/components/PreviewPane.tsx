"use client";

import { useEffect, useRef, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  type DocumentSummary,
  type DocumentVersion,
  type OcrResultSummary,
  type RenditionSummary,
  createWebdavEditToken,
  downloadDocumentVersion,
  downloadOcrPageImage,
  downloadRenditionContent,
  exportDocument,
  listDocumentVersions,
  listOcrResults,
  listRenditions,
  officeLaunchInfo,
  officeLaunchUrl,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

function triggerBrowserDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

type PreviewKind = "loading" | "image" | "text" | "html" | "pdf" | "none";

// Client-side direct display (P5d-S2, user feedback: `.txt`/`.json` had no
// working preview until now) - no new rendering-service renderer, no
// substitute-rendering overhead for content that's already text-based.
// `content_type` has come from server-side sniffing since P5d-S1, so it's
// more reliable than a value guessed by the client.
const MAX_PREVIEW_CHARS = 200_000;

// Polling for not-yet-ready renditions/OCR results (P23-S3, user request) -
// `rendering-service`/`ocr-service` only create their row once processing
// completes (no dedicated "pending" status in the API, see
// RenditionSummary/OcrResultSummary), so a not-yet-ready result is
// recognizable by the absence of the row, not by its status. Deliberately
// capped (instead of endless polling): a format without a supported
// rendition (e.g. an Office format with OCR disabled) would otherwise keep
// polling forever.
const RENDITION_POLL_INTERVAL_MS = 7_000;
const RENDITION_POLL_MAX_ATTEMPTS = 8; // ~56s total window

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isTextPreviewable(contentType: string | null): boolean {
  if (!contentType) return false;
  return contentType.startsWith("text/") || contentType === "application/json";
}

function findReadyRendition(
  renditions: RenditionSummary[],
  renditionType: string
): RenditionSummary | undefined {
  return renditions.find((r) => r.rendition_type === renditionType && r.status === "ready");
}

// Loads the page image of an OCR result; falls back to the existing
// rendering-service thumbnail rendition for raster images (which the OCR
// service processes without a dedicated page image, 409).
async function loadOcrOrThumbnailImage(
  token: string,
  ocrResult: OcrResultSummary,
  pageNumber: number,
  renditions: RenditionSummary[]
): Promise<Blob> {
  try {
    return await downloadOcrPageImage(token, ocrResult.id, pageNumber);
  } catch {
    // falls through to the rendition below (raster image case or error)
  }
  const thumbnail = findReadyRendition(renditions, "thumbnail");
  if (!thumbnail) throw new Error("keine Vorschau verfügbar");
  return downloadRenditionContent(token, thumbnail.id);
}

// Right column of the 3-column layout (user feedback after P4-S3, 8) -
// synchronized with the tab-controlled active document. Since P5-S2, loads
// the thumbnail substitute rendering produced by the rendering service;
// since P5-S3, additionally a version selector (user request) and a text
// overlay from the OCR service's word bounding boxes (user request: being
// able to select text in the preview, like with pdf.js). Substitute
// renderings/OCR are a bonus feature, not a blocker: if none exists (yet)
// (wrong format, processing not yet complete, load error), the column
// falls back to a hint text; the download button remains usable in any
// case.
export function PreviewPane({
  document: activeDocument,
  versionBump = 0,
}: {
  document: DocumentSummary | null;
  // Increments when another component (currently only `SignaturesPanel`,
  // P23-S7) outside this panel has created a new version of the same
  // document - triggers a reload of the version list, see the effect
  // below. Defaults to 0 for all call sites without their own bump state
  // (e.g. `PreviewEmptyContent`).
  versionBump?: number;
}) {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [previewKind, setPreviewKind] = useState<PreviewKind>("none");
  const [thumbnailUrl, setThumbnailUrl] = useState<string | null>(null);
  const [textContent, setTextContent] = useState<string | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [htmlContent, setHtmlContent] = useState<string | null>(null);
  const [versions, setVersions] = useState<DocumentVersion[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [ocrResult, setOcrResult] = useState<OcrResultSummary | null>(null);
  const [renditions, setRenditions] = useState<RenditionSummary[]>([]);
  const [selectedPage, setSelectedPage] = useState(1);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  // PDF export with export history (post-roadmap phase 28, ADR 0107) -
  // `null` means "use the installation default" (document-service
  // `GET /export-config`), an explicit choice overrides it for this export
  // only, same relationship as the backend's own default+override layering.
  const [exportHistoryPosition, setExportHistoryPosition] = useState<"before" | "after" | "">("");
  const [exportError, setExportError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const imgRef = useRef<HTMLImageElement>(null);
  const [imgRenderedHeight, setImgRenderedHeight] = useState(0);

  // Load the version list + reset the selection to the current version as
  // soon as a different document becomes active - since P23-S7 also on
  // every `versionBump` (e.g. after a signature from `SignaturesPanel`), so
  // an externally created new version shows up in the selector without
  // reopening the document.
  useEffect(() => {
    if (!activeDocument) {
      setVersions([]);
      setSelectedVersion(null);
      return;
    }
    setSelectedVersion(activeDocument.current_version_number);
    if (!accessToken) return;

    let cancelled = false;
    listDocumentVersions(accessToken, activeDocument.id)
      .then((result) => {
        if (!cancelled) setVersions(result);
      })
      .catch(() => {
        if (!cancelled) setVersions([]);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken, activeDocument?.id, versionBump]);

  // Load the preview for the selected version: either client-side direct
  // text display (P5d-S2) or the previous preview image + OCR result.
  // Waits for `versions` (content-type source) before deciding which
  // branch applies - the version effect above sets `selectedVersion`
  // immediately, but loads the metadata asynchronously afterward.
  useEffect(() => {
    if (!accessToken || !activeDocument || selectedVersion === null) {
      setPreviewKind("none");
      setThumbnailUrl(null);
      setTextContent(null);
      setPdfUrl(null);
      setHtmlContent(null);
      setOcrResult(null);
      setRenditions([]);
      setSelectedPage(1);
      return;
    }

    const versionMeta = versions.find((v) => v.version_number === selectedVersion);
    if (!versionMeta) return; // version metadata not loaded yet - don't show an intermediate state
    const contentType = versionMeta.content_type;

    let cancelled = false;
    let objectUrl: string | null = null;
    setPreviewKind("loading");
    setThumbnailUrl(null);
    setTextContent(null);
    setPdfUrl(null);
    setHtmlContent(null);
    setOcrResult(null);
    setRenditions([]);
    setDownloadError(null);
    setSelectedPage(1);

    async function load() {
      if (!accessToken || !activeDocument || selectedVersion === null) return;

      // Check HTML first (2.4, user feedback) - `text/html` would otherwise
      // match isTextPreviewable()'s `text/*` prefix and end up in the
      // plain-text branch (raw, escaped markup instead of a rendered page).
      if (contentType === "text/html") {
        try {
          const blob = await downloadDocumentVersion(accessToken, activeDocument.id, selectedVersion);
          if (cancelled) return;
          const text = await blob.text();
          setHtmlContent(text);
          setPreviewKind("html");
        } catch {
          if (!cancelled) setPreviewKind("none");
        }
        return;
      }

      if (isTextPreviewable(contentType)) {
        try {
          const blob = await downloadDocumentVersion(accessToken, activeDocument.id, selectedVersion);
          if (cancelled) return;
          const text = await blob.text();
          setTextContent(
            text.length > MAX_PREVIEW_CHARS ? `${text.slice(0, MAX_PREVIEW_CHARS)}\n…` : text
          );
          setPreviewKind("text");
        } catch {
          if (!cancelled) setPreviewKind("none");
        }
        return;
      }

      // Image and PDF sources don't expect a rendition here (their own,
      // more direct raw-byte branch further below) - only for everything
      // else (mainly Office/text formats) do we wait for `pdf_archive`/
      // `substitute_text`, polling if necessary (P23-S3, see
      // RENDITION_POLL_* above): `rendering-service` only creates the
      // rendition row AFTER the LibreOffice conversion completes, so a
      // still-empty result right after upload means "still in progress",
      // not "will never arrive".
      const expectsOfficeRendition =
        !contentType?.startsWith("image/") && contentType !== "application/pdf";
      let fetchedRenditions: RenditionSummary[] = [];
      let pdfArchive: RenditionSummary | undefined;
      let substitute: RenditionSummary | undefined;
      for (let attempt = 0; attempt <= RENDITION_POLL_MAX_ATTEMPTS; attempt++) {
        try {
          fetchedRenditions = await listRenditions(accessToken, activeDocument.id, selectedVersion);
        } catch {
          fetchedRenditions = [];
        }
        if (cancelled) return;
        setRenditions(fetchedRenditions);

        pdfArchive = expectsOfficeRendition
          ? findReadyRendition(fetchedRenditions, "pdf_archive")
          : undefined;
        substitute = pdfArchive ? undefined : findReadyRendition(fetchedRenditions, "substitute_text");

        if (pdfArchive || substitute || !expectsOfficeRendition) break;
        if (attempt === RENDITION_POLL_MAX_ATTEMPTS) break;
        await sleep(RENDITION_POLL_INTERVAL_MS);
        if (cancelled) return;
      }

      // Word-like A4 view (2.4, user feedback): rendering-service already
      // automatically converts Office/text formats via LibreOffice into a
      // real, paginated PDF (pdf_archive rendition, originally intended
      // only as an archive copy) - that's already the desired formatted
      // view, here consumed as a preview for the first time.
      if (pdfArchive) {
        try {
          const blob = await downloadRenditionContent(accessToken, pdfArchive.id);
          if (cancelled) return;
          objectUrl = URL.createObjectURL(blob);
          setPdfUrl(objectUrl);
          setPreviewKind("pdf");
        } catch {
          if (!cancelled) setPreviewKind("none");
        }
        return;
      }

      // Substitute rendering as text (DOCX/PPTX/ODS, 2.4) - for these,
      // rendering-service produces a "substitute_text" rendition instead
      // of a thumbnail; without this branch, the preview stayed empty for
      // these formats even though the rendition had long existed (user
      // feedback).
      if (substitute) {
        try {
          const blob = await downloadRenditionContent(accessToken, substitute.id);
          if (cancelled) return;
          const text = await blob.text();
          setTextContent(
            text.length > MAX_PREVIEW_CHARS ? `${text.slice(0, MAX_PREVIEW_CHARS)}\n…` : text
          );
          setPreviewKind("text");
        } catch {
          if (!cancelled) setPreviewKind("none");
        }
        return;
      }

      let ocr: OcrResultSummary | null = null;
      let ocrStillProcessing = false;
      try {
        const results = await listOcrResults(accessToken, activeDocument.id, selectedVersion);
        ocr = results.find((r) => r.status === "ready" || r.status === "needs_review") ?? null;
        // Just like with renditions, the OCR service only creates its row
        // after processing completes - for PDFs/images (the only
        // OCR-capable formats), an empty result means "still in progress",
        // not "will never arrive" (P23-S3). Only relevant if no OCR result
        // has been found yet - an already-present one (ready/
        // needs_review) needs no further reload.
        ocrStillProcessing =
          !ocr &&
          (contentType === "application/pdf" || !!contentType?.startsWith("image/")) &&
          results.every((r) => r.status !== "failed");
      } catch {
        // OCR is a bonus feature (3.9) - a load error only hides the text
        // overlay; the thumbnail is still attempted.
        ocr = null;
      }
      if (cancelled) return;
      if (ocrStillProcessing) {
        // Keeps running in the background AFTER the native PDF/image view
        // below has already been shown (the user sees something
        // immediately) - `ocrResult` is only set on success, so the
        // overlay appears afterward without interrupting the already
        // visible preview.
        void (async () => {
          for (let attempt = 0; attempt < RENDITION_POLL_MAX_ATTEMPTS; attempt++) {
            await sleep(RENDITION_POLL_INTERVAL_MS);
            if (cancelled || !accessToken || selectedVersion === null) return;
            let results: OcrResultSummary[] = [];
            try {
              results = await listOcrResults(accessToken, activeDocument.id, selectedVersion);
            } catch {
              return;
            }
            if (cancelled) return;
            const ready = results.find((r) => r.status === "ready" || r.status === "needs_review");
            if (ready) {
              setOcrResult(ready);
              return;
            }
            if (results.some((r) => r.status === "failed")) return;
          }
        })();
      }
      // PDFs: the page-image overlay remains only a fallback for real
      // scans without a text layer (engine "tesseract") - if OCR has
      // already embedded a real text layer (engine "native_text_layer",
      // see ocr-service text_layer.py) or no result exists yet, the native
      // PDF view below shows the real, searchable content directly from
      // the browser - no more need for the overlay detour. For images, the
      // existing overlay behavior remains unchanged (every engine, as
      // before).
      const useOverlay = !!ocr && (contentType !== "application/pdf" || ocr.engine === "tesseract");
      if (useOverlay && ocr) {
        // The actual page image is loaded by the separate effect below
        // (depends on `selectedPage`, already reset to 1 above) - this way
        // a later page change doesn't trigger a full reload.
        setOcrResult(ocr);
        return;
      }

      // Native PDF embed (2.4, user feedback) - primary view for PDFs with
      // a real text layer (either always present, or embedded by OCR),
      // fallback without a (finished) OCR result. Without this branch, a
      // PDF without an OCR result previously showed no preview at all.
      if (contentType === "application/pdf") {
        try {
          const blob = await downloadDocumentVersion(accessToken, activeDocument.id, selectedVersion);
          if (cancelled) return;
          objectUrl = URL.createObjectURL(blob);
          setPdfUrl(objectUrl);
          setPreviewKind("pdf");
        } catch {
          if (!cancelled) setPreviewKind("none");
        }
        return;
      }

      // Show real image documents at full resolution (2.4, user feedback)
      // instead of just the 256x256 thumbnail rendition - this branch only
      // applies if no OCR result was found above (scan overlay takes
      // precedence).
      if (contentType?.startsWith("image/")) {
        try {
          const blob = await downloadDocumentVersion(accessToken, activeDocument.id, selectedVersion);
          if (cancelled) return;
          objectUrl = URL.createObjectURL(blob);
          setThumbnailUrl(objectUrl);
          setPreviewKind("image");
        } catch {
          if (!cancelled) setPreviewKind("none");
        }
        return;
      }

      // All known substitute-rendering paths are covered above (thumbnail
      // renditions only exist for images, which already had their own
      // raw-byte branch above) - for everything else it stays at the hint
      // text; the download button remains usable in any case.
      setPreviewKind("none");
    }

    void load();

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken, activeDocument?.id, selectedVersion, versions]);

  // Loads/switches the page image of an OCR result, separate from the
  // effect above: a page change (user feedback: multi-page PDFs previously
  // always showed only page 1) should only reload a new page image, not
  // re-query the OCR result/renditions.
  useEffect(() => {
    if (!accessToken || !ocrResult) return;
    let cancelled = false;
    let objectUrl: string | null = null;
    setPreviewKind("loading");

    async function loadPage() {
      if (!accessToken || !ocrResult) return;
      try {
        const blob = await loadOcrOrThumbnailImage(accessToken, ocrResult, selectedPage, renditions);
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setThumbnailUrl(objectUrl);
        setPreviewKind("image");
      } catch {
        if (!cancelled) setPreviewKind("none");
      }
    }

    void loadPage();

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken, ocrResult, selectedPage]);

  // Measures the actually rendered image height so the overlay word spans
  // get the correct font size at any splitter width/zoom level.
  useEffect(() => {
    const el = imgRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) setImgRenderedHeight(entry.contentRect.height);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [thumbnailUrl]);

  async function handleDownload() {
    if (!accessToken || !activeDocument) return;
    const version = selectedVersion ?? activeDocument.current_version_number;
    setDownloadError(null);
    try {
      const blob = await downloadDocumentVersion(accessToken, activeDocument.id, version);
      triggerBrowserDownload(blob, activeDocument.title);
    } catch (error) {
      // Post-roadmap phase 19 session 11: previously failed completely
      // silently - especially for disposed (dehydrated) documents (409,
      // see document-service), this felt like clicking into a void. 409
      // now gets a targeted message with a retrieval hint, everything else
      // gets the generic fallback message.
      setDownloadError(
        error instanceof ApiError && error.status === 409
          ? t("preview.downloadErrorDehydrated")
          : t("preview.downloadErrorGeneric")
      );
    }
  }

  // PDF export with export history (post-roadmap phase 28, ADR 0107) -
  // unlike handleDownload, always exports the CURRENT version (the export
  // endpoint doesn't take a version number - export history is a
  // document-level concept, not tied to a single version).
  async function handleExport() {
    if (!accessToken || !activeDocument) return;
    setExportError(null);
    setExporting(true);
    try {
      const blob = await exportDocument(
        accessToken,
        activeDocument.id,
        exportHistoryPosition || undefined
      );
      triggerBrowserDownload(blob, `${activeDocument.title}-export.pdf`);
    } catch {
      setExportError(t("preview.exportErrorGeneric"));
    } finally {
      setExporting(false);
    }
  }

  // Direct Office editing (post-roadmap feature): open Word/Excel/PowerPoint
  // for editing directly from the browser, via the Office URI scheme
  // against webdav-connector - no password dialog, since a short-lived,
  // document-scoped token is embedded in the launch URL.
  async function handleOfficeLaunch() {
    if (!accessToken || !activeDocument) return;
    const launch = officeLaunchInfo(currentContentType);
    if (!launch) return;
    try {
      const { token: webdavToken } = await createWebdavEditToken(accessToken, activeDocument.id);
      const url = officeLaunchUrl(webdavToken, activeDocument.id, launch.ext);
      window.location.href = `${launch.scheme}:ofe|u|${url}`;
    } catch {
      // Deliberately no separate error area - same pattern as
      // handleDownload above.
    }
  }

  if (!activeDocument) {
    return (
      <section className="preview-pane" aria-label={t("preview.paneLabel")}>
        <p className="empty-state">{t("preview.noSelection")}</p>
      </section>
    );
  }

  const ocrPage = ocrResult?.pages.find((p) => p.page_number === selectedPage);
  const currentVersionMeta = versions.find((v) => v.version_number === selectedVersion);
  const currentContentType = currentVersionMeta?.content_type ?? null;

  // Since P16-S1 (dockable workspace), more than one `PreviewPane` can be
  // in the DOM at the same time (one panel per open document, "multiple
  // documents simultaneously visible and arrangeable", concept 8) - a
  // blanket `aria-label="Vorschau"` would no longer be unique for
  // screen-reader users. The title of the respective document makes each
  // instance distinguishable.
  return (
    <section
      className="preview-pane"
      aria-label={t("preview.paneLabelFor", { title: activeDocument.title })}
    >
      <h2 className="pane-heading">{activeDocument.title}</h2>

      {versions.length > 0 && (
        <>
          <label className="version-select">
            {t("preview.versionSelectLabel")}
            <select
              value={selectedVersion ?? activeDocument.current_version_number}
              onChange={(event) => setSelectedVersion(Number(event.target.value))}
            >
              {versions.map((version) => (
                <option
                  key={version.version_number}
                  value={version.version_number}
                  title={version.comment ?? undefined}
                >
                  {t("preview.versionOption", { number: version.version_number })}
                  {version.is_conflict ? ` ${t("preview.versionConflictMarker")}` : ""}
                </option>
              ))}
            </select>
          </label>
          {currentVersionMeta?.is_conflict && (
            <span
              className="badge down version-conflict-badge"
              title={t("preview.versionConflictTooltip")}
            >
              {t("preview.versionConflictBadge")}
            </span>
          )}
          {currentVersionMeta?.comment && (
            <span className="version-comment" title={currentVersionMeta.comment}>
              {t("preview.versionCommentLabel", { comment: currentVersionMeta.comment })}
            </span>
          )}
        </>
      )}

      {ocrResult && ocrResult.pages.length > 1 && (
        <label className="page-select">
          {t("preview.pageSelectLabel")}
          <select
            value={selectedPage}
            onChange={(event) => setSelectedPage(Number(event.target.value))}
          >
            {ocrResult.pages.map((page) => (
              <option key={page.page_number} value={page.page_number}>
                {t("preview.pageOption", { number: page.page_number })}
              </option>
            ))}
          </select>
        </label>
      )}

      {previewKind === "loading" && <p className="empty-state">{t("preview.loading")}</p>}

      {previewKind === "text" && textContent !== null && (
        <pre className="preview-text">{textContent}</pre>
      )}

      {previewKind === "image" && thumbnailUrl && (
        <div className="preview-image-wrapper">
          <img
            ref={imgRef}
            src={thumbnailUrl}
            alt={activeDocument.title}
            className="preview-thumbnail"
          />
          {ocrPage && (
            <div className="ocr-text-layer">
              {ocrPage.words.map((word, index) => (
                <span
                  key={index}
                  className="ocr-word"
                  style={{
                    left: `${(word.left / ocrPage.width) * 100}%`,
                    top: `${(word.top / ocrPage.height) * 100}%`,
                    width: `${(word.width / ocrPage.width) * 100}%`,
                    height: `${(word.height / ocrPage.height) * 100}%`,
                    fontSize: imgRenderedHeight
                      ? `${(word.height / ocrPage.height) * imgRenderedHeight}px`
                      : undefined,
                    lineHeight: imgRenderedHeight
                      ? `${(word.height / ocrPage.height) * imgRenderedHeight}px`
                      : undefined,
                  }}
                >
                  {word.text}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {previewKind === "image" &&
        currentContentType?.startsWith("image/") &&
        ocrResult?.full_text && (
          <details className="ocr-text-extract" open>
            <summary>{t("preview.ocrTextExtractLabel")}</summary>
            <pre className="preview-text">{ocrResult.full_text}</pre>
          </details>
        )}

      {previewKind === "pdf" && pdfUrl && (
        <embed
          src={pdfUrl}
          type="application/pdf"
          className="preview-pdf-frame"
          title={activeDocument.title}
        />
      )}

      {previewKind === "html" && htmlContent !== null && (
        <iframe
          className="preview-html-frame"
          sandbox=""
          referrerPolicy="no-referrer"
          srcDoc={htmlContent}
          title={activeDocument.title}
        />
      )}

      {previewKind === "none" && <p className="empty-state">{t("preview.noRendition")}</p>}

      {ocrResult?.status === "needs_review" && (
        <p className="ocr-review-hint">⚠ {t("preview.ocrNeedsReview")}</p>
      )}

      {downloadError && (
        <p className="error-text" role="alert">
          {downloadError}
        </p>
      )}

      <button type="button" onClick={handleDownload}>
        {t("folderBrowser.download")}
      </button>
      <label className="export-history-position">
        {t("preview.exportHistoryPositionLabel")}
        <select
          value={exportHistoryPosition}
          onChange={(event) =>
            setExportHistoryPosition(event.target.value as "before" | "after" | "")
          }
        >
          <option value="">{t("preview.exportHistoryPositionDefault")}</option>
          <option value="after">{t("preview.exportHistoryPositionAfter")}</option>
          <option value="before">{t("preview.exportHistoryPositionBefore")}</option>
        </select>
      </label>
      <button type="button" onClick={handleExport} disabled={exporting}>
        {exporting ? t("preview.exporting") : t("preview.export")}
      </button>
      {exportError && (
        <p className="error-text" role="alert">
          {exportError}
        </p>
      )}
      {officeLaunchInfo(currentContentType) && (
        <button type="button" onClick={handleOfficeLaunch}>
          {t("preview.openInOffice", { app: officeLaunchInfo(currentContentType)!.label })}
        </button>
      )}
    </section>
  );
}
