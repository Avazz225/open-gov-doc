"use client";

import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  getRedactionPreviewPageCount,
  getRedactionPreviewPageImage,
  redactDocument,
  type DocumentSummary,
  type RedactionRegion,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Document redaction (14.2, post-roadmap phase 31 session 4, ADR 0115) - one
// page at a time (Previous/Next), click-drag to draw a rectangle over the
// server-rasterized page image. Positioning is percentage-of-image, same
// technique already established for the OCR word overlay (PreviewPane.tsx)
// - only the drawing interaction itself (mousedown/move/up) is new.
export function RedactionModal({
  document: activeDocument,
  onClose,
  onRedacted,
}: {
  document: DocumentSummary;
  onClose: () => void;
  onRedacted: (redacted: DocumentSummary) => void;
}) {
  const { accessToken, user } = useAuth();
  const { t } = useI18n();
  const [pageCount, setPageCount] = useState<number | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [regions, setRegions] = useState<RedactionRegion[]>([]);
  const [drawStart, setDrawStart] = useState<{ x: number; y: number } | null>(null);
  const [drawCurrent, setDrawCurrent] = useState<{ x: number; y: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const imageRef = useRef<HTMLImageElement>(null);

  useEffect(() => {
    if (!accessToken) return;
    getRedactionPreviewPageCount(accessToken, activeDocument.id)
      .then(setPageCount)
      .catch(() => setError(t("redaction.loadError")));
  }, [accessToken, activeDocument.id, t]);

  useEffect(() => {
    if (!accessToken) return;
    let cancelled = false;
    let objectUrl: string | null = null;
    getRedactionPreviewPageImage(accessToken, activeDocument.id, pageNumber)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setImageUrl(objectUrl);
      })
      .catch(() => setError(t("redaction.loadError")));
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [accessToken, activeDocument.id, pageNumber, t]);

  function fractionFromEvent(event: ReactMouseEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 1),
      y: Math.min(Math.max((event.clientY - rect.top) / rect.height, 0), 1),
    };
  }

  function handleMouseDown(event: ReactMouseEvent<HTMLDivElement>) {
    const point = fractionFromEvent(event);
    setDrawStart(point);
    setDrawCurrent(point);
  }

  function handleMouseMove(event: ReactMouseEvent<HTMLDivElement>) {
    if (!drawStart) return;
    setDrawCurrent(fractionFromEvent(event));
  }

  function handleMouseUp() {
    if (!drawStart || !drawCurrent) return;
    const x = Math.min(drawStart.x, drawCurrent.x);
    const y = Math.min(drawStart.y, drawCurrent.y);
    const width = Math.abs(drawCurrent.x - drawStart.x);
    const height = Math.abs(drawCurrent.y - drawStart.y);
    // Ignore accidental clicks/tiny drags (matching the server's own
    // `gt=0` region validation intent, applied client-side too).
    if (width > 0.01 && height > 0.01) {
      setRegions((prev) => [...prev, { page_number: pageNumber, x, y, width, height }]);
    }
    setDrawStart(null);
    setDrawCurrent(null);
  }

  function removeRegion(index: number) {
    setRegions((prev) => prev.filter((_, i) => i !== index));
  }

  async function handleSubmit() {
    if (!accessToken || regions.length === 0) return;
    setError(null);
    setIsSubmitting(true);
    try {
      const redacted = await redactDocument(
        accessToken,
        activeDocument.id,
        regions,
        user?.username ?? ""
      );
      onRedacted(redacted);
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("redaction.submitError"));
    } finally {
      setIsSubmitting(false);
    }
  }

  const regionsOnCurrentPage = regions.filter((region) => region.page_number === pageNumber);
  const previewRect =
    drawStart && drawCurrent
      ? {
          x: Math.min(drawStart.x, drawCurrent.x),
          y: Math.min(drawStart.y, drawCurrent.y),
          width: Math.abs(drawCurrent.x - drawStart.x),
          height: Math.abs(drawCurrent.y - drawStart.y),
        }
      : null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-content modal-content-wide"
        role="dialog"
        aria-modal="true"
        aria-label={t("redaction.title")}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 className="pane-heading">{t("redaction.title")}</h2>
          <button
            type="button"
            className="modal-close"
            aria-label={t("upload.close")}
            onClick={onClose}
          >
            ×
          </button>
        </div>
        <p className="hint">{t("redaction.hint")}</p>

        {pageCount !== null && pageCount > 1 && (
          <div className="redaction-page-nav">
            <button
              type="button"
              onClick={() => setPageNumber((p) => Math.max(1, p - 1))}
              disabled={pageNumber <= 1}
            >
              {t("redaction.previousPage")}
            </button>
            <span>{t("redaction.pageIndicator", { current: pageNumber, total: pageCount })}</span>
            <button
              type="button"
              onClick={() => setPageNumber((p) => Math.min(pageCount, p + 1))}
              disabled={pageNumber >= pageCount}
            >
              {t("redaction.nextPage")}
            </button>
          </div>
        )}

        {imageUrl && (
          <div
            className="redaction-page-canvas"
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
          >
            <img
              ref={imageRef}
              src={imageUrl}
              alt={t("redaction.pageAlt", { page: pageNumber })}
              draggable={false}
            />
            {regionsOnCurrentPage.map((region, index) => (
              <div
                key={index}
                className="redaction-region"
                style={{
                  left: `${region.x * 100}%`,
                  top: `${region.y * 100}%`,
                  width: `${region.width * 100}%`,
                  height: `${region.height * 100}%`,
                }}
              />
            ))}
            {previewRect && (
              <div
                className="redaction-region redaction-region-preview"
                style={{
                  left: `${previewRect.x * 100}%`,
                  top: `${previewRect.y * 100}%`,
                  width: `${previewRect.width * 100}%`,
                  height: `${previewRect.height * 100}%`,
                }}
              />
            )}
          </div>
        )}

        {regions.length > 0 && (
          <ul className="redaction-region-list">
            {regions.map((region, index) => (
              <li key={index}>
                {t("redaction.regionEntry", { page: region.page_number })}
                <button type="button" onClick={() => removeRegion(index)}>
                  {t("common.delete")}
                </button>
              </li>
            ))}
          </ul>
        )}

        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}

        <div className="actions">
          <button type="button" onClick={handleSubmit} disabled={regions.length === 0 || isSubmitting}>
            {isSubmitting ? t("redaction.submitting") : t("redaction.submitAction")}
          </button>
          <button type="button" onClick={onClose}>
            {t("common.cancel")}
          </button>
        </div>
      </div>
    </div>
  );
}
