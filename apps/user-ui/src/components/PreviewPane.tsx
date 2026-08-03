"use client";

import { useEffect, useRef, useState } from "react";
import { useI18n } from "@/i18n";
import {
  type DocumentSummary,
  type DocumentVersion,
  type OcrResultSummary,
  type RenditionSummary,
  downloadDocumentVersion,
  downloadOcrPageImage,
  downloadRenditionContent,
  listDocumentVersions,
  listOcrResults,
  listRenditions,
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

type PreviewKind = "loading" | "image" | "text" | "none";

// Clientseitige Direktanzeige (P5d-S2, Nutzer-Feedback: `.txt`/`.json` hatten
// bislang keine funktionierende Vorschau) - kein neuer rendering-service-
// Renderer, kein Ersatzdarstellungs-Overhead für bereits textbasierte Inhalte.
// `content_type` kommt seit P5d-S1 aus dem serverseitigen Sniffing, ist also
// zuverlässiger als ein vom Client geratener Wert.
const MAX_PREVIEW_CHARS = 200_000;

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

// Lädt das Seitenbild eines OCR-Ergebnisses; fällt für Rasterbilder (die der
// OCR Service ohne eigenständiges Seitenbild verarbeitet, 409) auf die
// bestehende rendering-service-Thumbnail-Rendition zurück.
async function loadOcrOrThumbnailImage(
  token: string,
  ocrResult: OcrResultSummary,
  pageNumber: number,
  renditions: RenditionSummary[]
): Promise<Blob> {
  try {
    return await downloadOcrPageImage(token, ocrResult.id, pageNumber);
  } catch {
    // fällt durch auf die Rendition unten (Rasterbild-Fall oder Fehler)
  }
  const thumbnail = findReadyRendition(renditions, "thumbnail");
  if (!thumbnail) throw new Error("keine Vorschau verfügbar");
  return downloadRenditionContent(token, thumbnail.id);
}

// Rechte Spalte des 3-Spalten-Layouts (Nutzer-Feedback nach P4-S3, 8) - vom
// Tab-gesteuerten aktiven Dokument synchronisiert. Lädt seit P5-S2 die vom
// Rendering Service erzeugte Thumbnail-Ersatzdarstellung nach, seit P5-S3
// zusätzlich eine Versionsauswahl (Nutzerwunsch) und ein Text-Overlay aus den
// Wort-Bounding-Boxen des OCR Service (Nutzerwunsch: Text in der Vorschau
// markieren können, wie bei pdf.js). Ersatzdarstellungen/OCR sind ein
// Zusatznutzen, kein Blocker: existiert (noch) keine (falsches Format,
// Verarbeitung noch nicht abgeschlossen, Ladefehler), fällt die Spalte auf
// einen Hinweistext zurück, der Download-Button bleibt in jedem Fall nutzbar.
export function PreviewPane({ document: activeDocument }: { document: DocumentSummary | null }) {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [previewKind, setPreviewKind] = useState<PreviewKind>("none");
  const [thumbnailUrl, setThumbnailUrl] = useState<string | null>(null);
  const [textContent, setTextContent] = useState<string | null>(null);
  const [versions, setVersions] = useState<DocumentVersion[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [ocrResult, setOcrResult] = useState<OcrResultSummary | null>(null);
  const [renditions, setRenditions] = useState<RenditionSummary[]>([]);
  const [selectedPage, setSelectedPage] = useState(1);
  const imgRef = useRef<HTMLImageElement>(null);
  const [imgRenderedHeight, setImgRenderedHeight] = useState(0);

  // Versionsliste laden + Auswahl auf die aktuelle Version zurücksetzen,
  // sobald ein anderes Dokument aktiv wird.
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
  }, [accessToken, activeDocument?.id]);

  // Vorschau für die ausgewählte Version laden: entweder clientseitiger
  // Text-Direktanzeige (P5d-S2) oder das bisherige Vorschaubild + OCR-Ergebnis.
  // Wartet auf `versions` (Content-Type-Herkunft), bevor entschieden wird,
  // welcher Zweig greift - der Versions-Effekt oben setzt `selectedVersion`
  // sofort, lädt die Metadaten aber asynchron nach.
  useEffect(() => {
    if (!accessToken || !activeDocument || selectedVersion === null) {
      setPreviewKind("none");
      setThumbnailUrl(null);
      setTextContent(null);
      setOcrResult(null);
      setRenditions([]);
      setSelectedPage(1);
      return;
    }

    const versionMeta = versions.find((v) => v.version_number === selectedVersion);
    if (!versionMeta) return; // Versions-Metadaten noch nicht geladen - kein Zwischenzustand zeigen
    const contentType = versionMeta.content_type;

    let cancelled = false;
    let objectUrl: string | null = null;
    setPreviewKind("loading");
    setThumbnailUrl(null);
    setTextContent(null);
    setOcrResult(null);
    setRenditions([]);
    setSelectedPage(1);

    async function load() {
      if (!accessToken || !activeDocument || selectedVersion === null) return;

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

      let fetchedRenditions: RenditionSummary[] = [];
      try {
        fetchedRenditions = await listRenditions(accessToken, activeDocument.id, selectedVersion);
      } catch {
        fetchedRenditions = [];
      }
      if (cancelled) return;
      setRenditions(fetchedRenditions);

      // Ersatzdarstellung als Text (DOCX/PPTX/ODS, 2.4) - der
      // rendering-service erzeugt dafür eine "substitute_text"-Rendition
      // statt eines Thumbnails; ohne diesen Zweig blieb die Vorschau für
      // diese Formate leer, obwohl die Rendition längst existierte
      // (Nutzer-Feedback).
      const substitute = findReadyRendition(fetchedRenditions, "substitute_text");
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
      try {
        const results = await listOcrResults(accessToken, activeDocument.id, selectedVersion);
        ocr = results.find((r) => r.status === "ready" || r.status === "needs_review") ?? null;
      } catch {
        // OCR ist ein Zusatznutzen (3.9) - ein Ladefehler blendet nur das
        // Text-Overlay aus, das Thumbnail wird trotzdem versucht.
        ocr = null;
      }
      if (cancelled) return;
      if (ocr) {
        // Das eigentliche Seitenbild lädt der separate Effekt unten (hängt
        // von `selectedPage` ab, oben bereits auf 1 zurückgesetzt) - so löst
        // ein späterer Seitenwechsel keinen kompletten Reload aus.
        setOcrResult(ocr);
        return;
      }

      const thumbnail = findReadyRendition(fetchedRenditions, "thumbnail");
      if (!thumbnail) {
        setPreviewKind("none");
        return;
      }
      try {
        const blob = await downloadRenditionContent(accessToken, thumbnail.id);
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setThumbnailUrl(objectUrl);
        setPreviewKind("image");
      } catch {
        if (!cancelled) setPreviewKind("none");
      }
    }

    void load();

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken, activeDocument?.id, selectedVersion, versions]);

  // Lädt/wechselt das Seitenbild eines OCR-Ergebnisses, getrennt vom Effekt
  // oben: ein Seitenwechsel (Nutzer-Feedback: mehrseitige PDFs zeigten bisher
  // immer nur Seite 1) soll nur ein neues Seitenbild nachladen, nicht erneut
  // OCR-Ergebnis/Renditionen abfragen.
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

  // Misst die tatsächlich gerenderte Bildhöhe, damit die Overlay-Wort-Spans
  // bei jeder Splitter-Breite/jedem Zoom die richtige Schriftgröße bekommen.
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
    try {
      const blob = await downloadDocumentVersion(accessToken, activeDocument.id, version);
      triggerBrowserDownload(blob, activeDocument.title);
    } catch {
      // Download-Fehler an dieser Stelle bewusst nicht separat behandelt -
      // die Explorer-Spalte zeigt bereits einen globalen Fehlerbereich.
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

  return (
    <section className="preview-pane" aria-label={t("preview.paneLabel")}>
      <h2 className="pane-heading">{activeDocument.title}</h2>

      {versions.length > 1 && (
        <label className="version-select">
          {t("preview.versionSelectLabel")}
          <select
            value={selectedVersion ?? activeDocument.current_version_number}
            onChange={(event) => setSelectedVersion(Number(event.target.value))}
          >
            {versions.map((version) => (
              <option key={version.version_number} value={version.version_number}>
                {t("preview.versionOption", { number: version.version_number })}
              </option>
            ))}
          </select>
        </label>
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

      {previewKind === "none" && <p className="empty-state">{t("preview.noRendition")}</p>}

      {ocrResult?.status === "needs_review" && (
        <p className="ocr-review-hint">⚠ {t("preview.ocrNeedsReview")}</p>
      )}

      <button type="button" onClick={handleDownload}>
        {t("folderBrowser.download")}
      </button>
    </section>
  );
}
