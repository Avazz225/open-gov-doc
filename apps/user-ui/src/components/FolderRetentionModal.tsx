"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  createFolderLegalHold,
  listFolderLegalHolds,
  putFolderRetention,
  releaseFolderLegalHold,
  type Folder,
  type FolderLegalHold,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

function toDateInputValue(iso: string | null): string {
  if (!iso) return "";
  return iso.slice(0, 10);
}

// Aufbewahrung/Legal Hold/Zwangslöschung für Ordner (5.2/5.2a, seit
// P7-S1b) - inhaltlich identisch zu `RetentionPanel.tsx` (Dokumente, P7-S1),
// aber als Modal statt als Anbau unter einem dauerhaften Metadaten-Panel, da
// es für Ordner (anders als für das aktuell offene Dokument) noch kein
// solches Panel gibt. Erreichbar über ein neues Icon je Ordnerzeile in
// `ExplorerPane.tsx`, wiederverwendet die bereits vorhandenen
// `.modal-backdrop`/`.modal-content`-Klassen (siehe `UploadForm.tsx`).
export function FolderRetentionModal({ folder, onClose }: { folder: Folder; onClose: () => void }) {
  const { accessToken, user } = useAuth();
  const { t } = useI18n();
  const [retentionUntil, setRetentionUntil] = useState(toDateInputValue(folder.retention_until));
  const [fullDeletion, setFullDeletion] = useState(folder.full_deletion);
  const [reason, setReason] = useState(folder.pending_deletion_reason ?? "");
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const [holds, setHolds] = useState<FolderLegalHold[]>([]);
  const [holdReason, setHoldReason] = useState("");
  const [isHoldBusy, setIsHoldBusy] = useState(false);

  useEffect(() => {
    if (!accessToken) return;
    listFolderLegalHolds(accessToken, folder.id, true)
      .then(setHolds)
      .catch(() => setHolds([]));
  }, [accessToken, folder.id]);

  const activeHold = holds.find((h) => h.released_at === null) ?? null;

  async function handleSubmit() {
    if (!accessToken) return;
    setError(null);
    setSaved(false);
    setIsSaving(true);
    try {
      await putFolderRetention(accessToken, folder.id, {
        retentionUntil: retentionUntil ? new Date(retentionUntil).toISOString() : null,
        fullDeletion,
        reason: reason.trim() || null,
      });
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("retention.saveError"));
    } finally {
      setIsSaving(false);
    }
  }

  async function handleSetHold() {
    if (!accessToken || !user) return;
    setIsHoldBusy(true);
    setError(null);
    try {
      const hold = await createFolderLegalHold(accessToken, {
        folderId: folder.id,
        setBy: user.username,
        reason: holdReason.trim() || null,
      });
      setHolds((prev) => [hold, ...prev]);
      setHoldReason("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("retention.holdError"));
    } finally {
      setIsHoldBusy(false);
    }
  }

  async function handleReleaseHold() {
    if (!accessToken || !user || !activeHold) return;
    setIsHoldBusy(true);
    setError(null);
    try {
      const released = await releaseFolderLegalHold(accessToken, activeHold.id, user.username);
      setHolds((prev) => prev.map((h) => (h.id === released.id ? released : h)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("retention.holdError"));
    } finally {
      setIsHoldBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-content"
        role="dialog"
        aria-modal="true"
        aria-label={t("folderRetention.heading", { name: folder.name })}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 className="pane-heading">{t("folderRetention.heading", { name: folder.name })}</h2>
          <button type="button" className="modal-close" aria-label={t("common.close")} onClick={onClose}>
            ×
          </button>
        </div>

        {activeHold ? (
          <div className="legal-hold-active">
            <p className="hint">
              {t("retention.legalHoldActive", { setBy: activeHold.set_by })}
              {activeHold.reason ? ` (${activeHold.reason})` : ""}
            </p>
            <button type="button" onClick={handleReleaseHold} disabled={isHoldBusy}>
              {t("retention.releaseLegalHold")}
            </button>
          </div>
        ) : (
          <div className="legal-hold-form">
            <label>
              {t("retention.legalHoldReasonLabel")}
              <input value={holdReason} onChange={(e) => setHoldReason(e.target.value)} />
            </label>
            <button type="button" onClick={handleSetHold} disabled={isHoldBusy}>
              {t("retention.setLegalHold")}
            </button>
          </div>
        )}

        <label>
          {t("retention.retentionUntilLabel")}
          <input
            type="date"
            value={retentionUntil}
            onChange={(e) => setRetentionUntil(e.target.value)}
          />
        </label>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={fullDeletion}
            onChange={(e) => setFullDeletion(e.target.checked)}
          />
          {t("retention.fullDeletionLabel")}
        </label>
        {fullDeletion && (
          <label>
            {t("retention.reasonLabel")}
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
        )}
        <button type="button" onClick={handleSubmit} disabled={isSaving}>
          {isSaving ? t("retention.saving") : t("retention.save")}
        </button>

        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        {saved && !error && <p className="hint">{t("retention.saved")}</p>}
      </div>
    </div>
  );
}
