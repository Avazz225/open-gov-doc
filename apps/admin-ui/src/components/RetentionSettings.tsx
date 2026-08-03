"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  getRetentionConfig,
  getTrashConfig,
  updateRetentionConfig,
  updateTrashConfig,
  type RetentionConfig,
  type TrashConfig,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Aufbewahrung/Legal Hold/Zwangslöschung (5.2/5.2a, seit P7-S1) - gleiches
// Lade-/Speicher-Muster wie UploadSettings/OcrSettings, hier zwei getrennte
// Configs (document-service) in einem gemeinsamen Formular.
export function RetentionSettings() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [retentionConfig, setRetentionConfig] = useState<RetentionConfig | null>(null);
  const [trashConfig, setTrashConfig] = useState<TrashConfig | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const [deletionReasonRequired, setDeletionReasonRequired] = useState(false);
  const [reminderLeadDaysInput, setReminderLeadDaysInput] = useState("");
  const [restorePeriodDaysInput, setRestorePeriodDaysInput] = useState("30");

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      const [retention, trash] = await Promise.all([
        getRetentionConfig(accessToken),
        getTrashConfig(accessToken),
      ]);
      setRetentionConfig(retention);
      setTrashConfig(trash);
      setDeletionReasonRequired(retention.deletion_reason_required);
      setReminderLeadDaysInput(
        retention.reminder_lead_days === null ? "" : String(retention.reminder_lead_days)
      );
      setRestorePeriodDaysInput(String(trash.restore_period_days));
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
      const reminderLeadDays =
        reminderLeadDaysInput.trim() === "" ? null : Number(reminderLeadDaysInput);
      const [retention, trash] = await Promise.all([
        updateRetentionConfig(accessToken, {
          deletionReasonRequired,
          reminderLeadDays,
        }),
        updateTrashConfig(accessToken, {
          restorePeriodDays: Number(restorePeriodDaysInput),
        }),
      ]);
      setRetentionConfig(retention);
      setTrashConfig(trash);
      setSavedAt(Date.now());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="card">
      <p className="hint">{t("retentionSettings.hint")}</p>

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : unreachable ? (
        <p className="empty-state">{t("retentionSettings.unreachable")}</p>
      ) : (
        <form className="form-grid" onSubmit={handleSubmit}>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={deletionReasonRequired}
              onChange={(e) => setDeletionReasonRequired(e.target.checked)}
            />
            {t("retentionSettings.deletionReasonRequired")}
          </label>
          <label>
            {t("retentionSettings.reminderLeadDays")}
            <input
              type="number"
              min="0"
              value={reminderLeadDaysInput}
              onChange={(e) => setReminderLeadDaysInput(e.target.value)}
              placeholder={t("retentionSettings.reminderLeadDaysPlaceholder")}
            />
          </label>
          <label>
            {t("retentionSettings.restorePeriodDays")}
            <input
              type="number"
              min="0"
              required
              value={restorePeriodDaysInput}
              onChange={(e) => setRestorePeriodDaysInput(e.target.value)}
            />
          </label>
          <div className="actions">
            <button type="submit" disabled={isSaving}>
              {t("common.save")}
            </button>
          </div>
        </form>
      )}

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {savedAt !== null && !error && <p className="hint">{t("retentionSettings.saved")}</p>}
      {retentionConfig && trashConfig && (
        <p className="hint">
          {t("retentionSettings.updatedAt")}:{" "}
          {new Date(retentionConfig.updated_at).toLocaleString()}
        </p>
      )}
    </div>
  );
}
