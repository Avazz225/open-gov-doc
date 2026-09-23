"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  getExportConfig,
  updateExportConfig,
  type ExportConfig,
  type ExportHistoryPosition,
  type ExportStampPosition,
  type ExportStampType,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// PDF export configuration (14.x, Post-Roadmap Phase 28, ADR 0107) plus
// output stamping (Post-Roadmap Phase 31 Session 6, ADR 0117) - same load/
// save/empty-state pattern as OcrSettings/UploadSettings. Both fields groups
// are already live in the export pipeline (`POST /documents/{id}/export`,
// `POST /folders/{id}/export`) - this page was the only missing piece
// (API-only since both phases), closing the gap named in the Phase 32+ plan.
// `stamp_position="diagonal-center"` is only valid for `stamp_type="text"`
// and `stamp_value_template` only accepts the `{document_id}`/`{kennzeichen}`
// placeholders - both rules are enforced server-side (`422`), not
// duplicated here client-side, same minimalism as the other settings pages.
export function ExportSettings() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [config, setConfig] = useState<ExportConfig | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const [historyPosition, setHistoryPosition] = useState<ExportHistoryPosition>("after");
  const [stampEnabled, setStampEnabled] = useState(false);
  const [stampType, setStampType] = useState<ExportStampType>("qr");
  const [stampValueTemplate, setStampValueTemplate] = useState("{kennzeichen}");
  const [stampPosition, setStampPosition] = useState<ExportStampPosition>("bottom-right");

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      const loaded = await getExportConfig(accessToken);
      setConfig(loaded);
      setHistoryPosition(loaded.history_position);
      setStampEnabled(loaded.stamp_enabled);
      setStampType(loaded.stamp_type);
      setStampValueTemplate(loaded.stamp_value_template);
      setStampPosition(loaded.stamp_position);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setUnreachable(true);
      }
    } finally {
      setIsLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    setError(null);
    setSavedAt(null);
    setIsSaving(true);
    try {
      const updated = await updateExportConfig(accessToken, {
        historyPosition,
        stampEnabled,
        stampType,
        stampValueTemplate,
        stampPosition,
      });
      setConfig(updated);
      setSavedAt(Date.now());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="rounded-lg border border-border p-4 mb-6">
      <p className="text-sm opacity-80">{t("exportSettings.hint")}</p>

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : unreachable ? (
        <p className="italic opacity-70">{t("exportSettings.unreachable")}</p>
      ) : (
        <form className="form-grid" onSubmit={handleSubmit}>
          <label>
            {t("exportSettings.historyPosition")}
            <select
              value={historyPosition}
              onChange={(event) =>
                setHistoryPosition(event.target.value as ExportHistoryPosition)
              }
            >
              <option value="before">{t("exportSettings.historyPositionBefore")}</option>
              <option value="after">{t("exportSettings.historyPositionAfter")}</option>
            </select>
          </label>

          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={stampEnabled}
              onChange={(event) => setStampEnabled(event.target.checked)}
            />
            {t("exportSettings.stampEnabled")}
          </label>
          <p className="col-span-full text-sm opacity-80">{t("exportSettings.stampEnabledHint")}</p>

          <label>
            {t("exportSettings.stampType")}
            <select
              value={stampType}
              onChange={(event) => setStampType(event.target.value as ExportStampType)}
            >
              <option value="text">{t("exportSettings.stampTypeText")}</option>
              <option value="qr">{t("exportSettings.stampTypeQr")}</option>
              <option value="barcode">{t("exportSettings.stampTypeBarcode")}</option>
            </select>
          </label>
          <label>
            {t("exportSettings.stampPosition")}
            <select
              value={stampPosition}
              onChange={(event) => setStampPosition(event.target.value as ExportStampPosition)}
            >
              <option value="diagonal-center">{t("exportSettings.stampPositionDiagonal")}</option>
              <option value="top-left">{t("exportSettings.stampPositionTopLeft")}</option>
              <option value="top-right">{t("exportSettings.stampPositionTopRight")}</option>
              <option value="bottom-left">{t("exportSettings.stampPositionBottomLeft")}</option>
              <option value="bottom-right">{t("exportSettings.stampPositionBottomRight")}</option>
            </select>
          </label>
          <p className="col-span-full text-sm opacity-80">{t("exportSettings.stampPositionHint")}</p>

          <label>
            {t("exportSettings.stampValueTemplate")}
            <input
              type="text"
              value={stampValueTemplate}
              onChange={(event) => setStampValueTemplate(event.target.value)}
            />
          </label>
          <p className="col-span-full text-sm opacity-80">{t("exportSettings.stampValueTemplateHint")}</p>

          <div className="flex gap-2">
            <button type="submit" disabled={isSaving}>
              {t("common.save")}
            </button>
          </div>
        </form>
      )}

      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
      {savedAt !== null && !error && <p className="text-sm opacity-80">{t("exportSettings.saved")}</p>}
      {config && (
        <p className="text-sm opacity-80">
          {t("exportSettings.updatedAt")}: {new Date(config.updated_at).toLocaleString()}
        </p>
      )}
    </div>
  );
}
