"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  getFolderRetentionConfig,
  getFolderTrashConfig,
  getRetentionConfig,
  getTrashConfig,
  updateFolderRetentionConfig,
  updateFolderTrashConfig,
  updateRetentionConfig,
  updateTrashConfig,
  type RetentionConfig,
  type TrashConfig,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

interface SectionState {
  retentionConfig: RetentionConfig | null;
  trashConfig: TrashConfig | null;
  unreachable: boolean;
  error: string | null;
  isLoading: boolean;
  isSaving: boolean;
  savedAt: number | null;
  deletionReasonRequired: boolean;
  reminderLeadDaysInput: string;
  restorePeriodDaysInput: string;
}

const initialSectionState: SectionState = {
  retentionConfig: null,
  trashConfig: null,
  unreachable: false,
  error: null,
  isLoading: true,
  isSaving: false,
  savedAt: null,
  deletionReasonRequired: false,
  reminderLeadDaysInput: "",
  restorePeriodDaysInput: "30",
};

// Aufbewahrung/Legal Hold/Zwangslöschung (5.2/5.2a, seit P7-S1) - gleiches
// Lade-/Speicher-Muster wie UploadSettings/OcrSettings. Seit P7-S1b zwei
// unabhängige Sektionen (Dokumente über document-service, Ordner über
// folder-service - eigene, unabhängig konfigurierbare Configs, siehe
// docs/services/folder-service.md), damit z. B. eine andere
// Papierkorb-Frist für Ordner als für Dokumente möglich ist.
function RetentionSection({
  heading,
  hint,
  unreachableLabel,
  get,
  update,
}: {
  heading: string;
  hint?: string;
  unreachableLabel: string;
  get: () => Promise<{ retention: RetentionConfig; trash: TrashConfig }>;
  update: (payload: {
    deletionReasonRequired: boolean;
    reminderLeadDays: number | null;
    restorePeriodDays: number;
  }) => Promise<{ retention: RetentionConfig; trash: TrashConfig }>;
}) {
  const { t } = useI18n();
  const [state, setState] = useState<SectionState>(initialSectionState);

  const reload = useCallback(async () => {
    setState((prev) => ({ ...prev, isLoading: true, unreachable: false, error: null }));
    try {
      const { retention, trash } = await get();
      setState((prev) => ({
        ...prev,
        retentionConfig: retention,
        trashConfig: trash,
        deletionReasonRequired: retention.deletion_reason_required,
        reminderLeadDaysInput:
          retention.reminder_lead_days === null ? "" : String(retention.reminder_lead_days),
        restorePeriodDaysInput: String(trash.restore_period_days),
        isLoading: false,
      }));
    } catch (err) {
      setState((prev) => ({
        ...prev,
        isLoading: false,
        unreachable: !(err instanceof ApiError),
        error: err instanceof ApiError ? err.message : null,
      }));
    }
  }, [get]);

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setState((prev) => ({ ...prev, error: null, savedAt: null, isSaving: true }));
    try {
      const reminderLeadDays =
        state.reminderLeadDaysInput.trim() === "" ? null : Number(state.reminderLeadDaysInput);
      const { retention, trash } = await update({
        deletionReasonRequired: state.deletionReasonRequired,
        reminderLeadDays,
        restorePeriodDays: Number(state.restorePeriodDaysInput),
      });
      setState((prev) => ({
        ...prev,
        retentionConfig: retention,
        trashConfig: trash,
        savedAt: Date.now(),
        isSaving: false,
      }));
    } catch (err) {
      setState((prev) => ({
        ...prev,
        error: err instanceof ApiError ? err.message : t("common.loadError"),
        isSaving: false,
      }));
    }
  }

  return (
    <div className="card">
      <h3>{heading}</h3>
      {hint && <p className="hint">{hint}</p>}

      {state.isLoading ? (
        <p>{t("common.loading")}</p>
      ) : state.unreachable ? (
        <p className="empty-state">{unreachableLabel}</p>
      ) : (
        <form className="form-grid" onSubmit={handleSubmit}>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={state.deletionReasonRequired}
              onChange={(e) =>
                setState((prev) => ({ ...prev, deletionReasonRequired: e.target.checked }))
              }
            />
            {t("retentionSettings.deletionReasonRequired")}
          </label>
          <label>
            {t("retentionSettings.reminderLeadDays")}
            <input
              type="number"
              min="0"
              value={state.reminderLeadDaysInput}
              onChange={(e) =>
                setState((prev) => ({ ...prev, reminderLeadDaysInput: e.target.value }))
              }
              placeholder={t("retentionSettings.reminderLeadDaysPlaceholder")}
            />
          </label>
          <label>
            {t("retentionSettings.restorePeriodDays")}
            <input
              type="number"
              min="0"
              required
              value={state.restorePeriodDaysInput}
              onChange={(e) =>
                setState((prev) => ({ ...prev, restorePeriodDaysInput: e.target.value }))
              }
            />
          </label>
          <div className="actions">
            <button type="submit" disabled={state.isSaving}>
              {t("common.save")}
            </button>
          </div>
        </form>
      )}

      {state.error && (
        <p className="error-text" role="alert">
          {state.error}
        </p>
      )}
      {state.savedAt !== null && !state.error && (
        <p className="hint">{t("retentionSettings.saved")}</p>
      )}
      {state.retentionConfig && state.trashConfig && (
        <p className="hint">
          {t("retentionSettings.updatedAt")}:{" "}
          {new Date(state.retentionConfig.updated_at).toLocaleString()}
        </p>
      )}
    </div>
  );
}

export function RetentionSettings() {
  const { accessToken } = useAuth();
  const { t } = useI18n();

  if (!accessToken) return null;

  return (
    <>
      <p className="hint">{t("retentionSettings.hint")}</p>
      <RetentionSection
        heading={t("retentionSettings.documentsHeading")}
        unreachableLabel={t("retentionSettings.unreachable")}
        get={async () => {
          const [retention, trash] = await Promise.all([
            getRetentionConfig(accessToken),
            getTrashConfig(accessToken),
          ]);
          return { retention, trash };
        }}
        update={async (payload) => {
          const [retention, trash] = await Promise.all([
            updateRetentionConfig(accessToken, {
              deletionReasonRequired: payload.deletionReasonRequired,
              reminderLeadDays: payload.reminderLeadDays,
            }),
            updateTrashConfig(accessToken, { restorePeriodDays: payload.restorePeriodDays }),
          ]);
          return { retention, trash };
        }}
      />
      <RetentionSection
        heading={t("retentionSettings.foldersHeading")}
        hint={t("retentionSettings.foldersHint")}
        unreachableLabel={t("retentionSettings.foldersUnreachable")}
        get={async () => {
          const [retention, trash] = await Promise.all([
            getFolderRetentionConfig(accessToken),
            getFolderTrashConfig(accessToken),
          ]);
          return { retention, trash };
        }}
        update={async (payload) => {
          const [retention, trash] = await Promise.all([
            updateFolderRetentionConfig(accessToken, {
              deletionReasonRequired: payload.deletionReasonRequired,
              reminderLeadDays: payload.reminderLeadDays,
            }),
            updateFolderTrashConfig(accessToken, { restorePeriodDays: payload.restorePeriodDays }),
          ]);
          return { retention, trash };
        }}
      />
    </>
  );
}
