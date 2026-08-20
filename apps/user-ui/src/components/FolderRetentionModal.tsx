"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  createFolderLegalHold,
  getFolderRetentionConfig,
  listFolderLegalHolds,
  putFolderRetention,
  releaseFolderLegalHold,
  type Folder,
  type FolderLegalHold,
  type RetentionConfig,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Sentinel select value for "reason not in the admin-curated catalog" - see
// RetentionPanel.tsx (post-roadmap phase 31 session 1, ADR 0112).
const OTHER_REASON = "__other__";

function toDateInputValue(iso: string | null): string {
  if (!iso) return "";
  return iso.slice(0, 10);
}

// Retention/legal hold/forced deletion for folders (5.2/5.2a, since
// P7-S1b) - functionally identical to `RetentionPanel.tsx` (documents,
// P7-S1), but as a modal instead of an addition under a persistent metadata
// panel, since no such panel exists yet for folders (unlike for the
// currently open document). Reachable via a new icon per folder row in
// `ExplorerPane.tsx`, reuses the already-existing `.modal-backdrop`/
// `.modal-content` classes (see `UploadForm.tsx`).
export function FolderRetentionModal({ folder, onClose }: { folder: Folder; onClose: () => void }) {
  const { accessToken, user, permissions } = useAuth();
  const { t } = useI18n();
  // RBAC (post-roadmap phase 19 session 10, ADR 0075) - see RetentionPanel.tsx.
  const canManageLegalHold = permissions.includes("admin.legal_hold");
  const [retentionUntil, setRetentionUntil] = useState(toDateInputValue(folder.retention_until));
  const [fullDeletion, setFullDeletion] = useState(folder.full_deletion);
  const [reason, setReason] = useState(folder.pending_deletion_reason ?? "");
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [retentionConfig, setRetentionConfig] = useState<RetentionConfig | null>(null);
  const [reasonMode, setReasonMode] = useState<"catalog" | "other">("other");

  const [holds, setHolds] = useState<FolderLegalHold[]>([]);
  const [holdReason, setHoldReason] = useState("");
  const [isHoldBusy, setIsHoldBusy] = useState(false);

  useEffect(() => {
    if (!accessToken) return;
    listFolderLegalHolds(accessToken, folder.id, true)
      .then(setHolds)
      .catch(() => setHolds([]));
  }, [accessToken, folder.id]);

  useEffect(() => {
    if (!accessToken) return;
    getFolderRetentionConfig(accessToken)
      .then((config) => {
        setRetentionConfig(config);
        const currentReason = folder.pending_deletion_reason ?? "";
        // Empty (no reason chosen yet) defaults to the dropdown, not the
        // free-text fallback - otherwise both would show at once on first load.
        setReasonMode(
          !currentReason || config.deletion_reason_catalog.includes(currentReason)
            ? "catalog"
            : "other"
        );
      })
      .catch(() => setRetentionConfig(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
            <button
              type="button"
              onClick={handleReleaseHold}
              disabled={isHoldBusy || !canManageLegalHold}
              title={canManageLegalHold ? undefined : t("retention.legalHoldPermissionHint")}
            >
              {t("retention.releaseLegalHold")}
            </button>
          </div>
        ) : (
          <div className="legal-hold-form">
            <label>
              {t("retention.legalHoldReasonLabel")}
              <input
                value={holdReason}
                onChange={(e) => setHoldReason(e.target.value)}
                disabled={!canManageLegalHold}
              />
            </label>
            <button
              type="button"
              onClick={handleSetHold}
              disabled={isHoldBusy || !canManageLegalHold}
              title={canManageLegalHold ? undefined : t("retention.legalHoldPermissionHint")}
            >
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
        {fullDeletion && retentionConfig && retentionConfig.deletion_reason_catalog.length > 0 && (
          <label>
            {t("retention.reasonLabel")}
            <select
              value={reasonMode === "catalog" ? reason : OTHER_REASON}
              onChange={(e) => {
                if (e.target.value === OTHER_REASON) {
                  setReasonMode("other");
                  setReason("");
                } else {
                  setReasonMode("catalog");
                  setReason(e.target.value);
                }
              }}
            >
              <option value="" disabled>
                {t("retention.reasonSelectPlaceholder")}
              </option>
              {retentionConfig.deletion_reason_catalog.map((entry) => (
                <option key={entry} value={entry}>
                  {entry}
                </option>
              ))}
              <option value={OTHER_REASON}>{t("retention.reasonOther")}</option>
            </select>
          </label>
        )}
        {fullDeletion &&
          (reasonMode === "other" ||
            !retentionConfig ||
            retentionConfig.deletion_reason_catalog.length === 0) && (
            <label>
              {retentionConfig && retentionConfig.deletion_reason_catalog.length > 0
                ? t("retention.reasonOtherLabel")
                : t("retention.reasonLabel")}
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
