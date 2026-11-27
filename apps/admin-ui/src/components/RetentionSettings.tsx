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
  deletionReasonCatalog: string[];
  newReasonInput: string;
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
  deletionReasonCatalog: [],
  newReasonInput: "",
};

// Retention/legal hold/forced deletion (5.2/5.2a, since P7-S1) - same
// load/save pattern as UploadSettings/OcrSettings. Since P7-S1b two
// independent sections (documents via document-service, folders via
// folder-service - separate, independently configurable configs, see
// docs/services/folder-service.md), so that e.g. a different trash
// retention period is possible for folders than for documents.
function RetentionSection({
  heading,
  hint,
  unreachableLabel,
  get,
  update,
  onSaved,
}: {
  heading: string;
  hint?: string;
  unreachableLabel: string;
  onSaved?: () => void;
  get: () => Promise<{ retention: RetentionConfig; trash: TrashConfig }>;
  update: (payload: {
    deletionReasonRequired: boolean;
    reminderLeadDays: number | null;
    restorePeriodDays: number;
    deletionReasonCatalog: string[];
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
        deletionReasonCatalog: retention.deletion_reason_catalog,
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
        deletionReasonCatalog: state.deletionReasonCatalog,
      });
      setState((prev) => ({
        ...prev,
        retentionConfig: retention,
        trashConfig: trash,
        savedAt: Date.now(),
        isSaving: false,
      }));
      onSaved?.();
    } catch (err) {
      setState((prev) => ({
        ...prev,
        error: err instanceof ApiError ? err.message : t("common.loadError"),
        isSaving: false,
      }));
    }
  }

  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
  const fieldInput =
    "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";
  const checkbox = "h-4 w-4 rounded border-border accent-accent";

  return (
    <div className="rounded-lg border border-border p-4 mb-6">
      <h3>{heading}</h3>
      {hint && <p className="text-sm opacity-80">{hint}</p>}

      {state.isLoading ? (
        <p>{t("common.loading")}</p>
      ) : state.unreachable ? (
        <p className="italic opacity-70">{unreachableLabel}</p>
      ) : (
        <form className="form-grid" onSubmit={handleSubmit}>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={state.deletionReasonRequired}
              onChange={(e) =>
                setState((prev) => ({ ...prev, deletionReasonRequired: e.target.checked }))
              }
              className={checkbox}
            />
            {t("retentionSettings.deletionReasonRequired")}
          </label>

          <div className="deletion-reason-catalog">
            <span className="text-sm opacity-80">{t("retentionSettings.deletionReasonCatalogHint")}</span>
            {state.deletionReasonCatalog.length === 0 ? (
              <p className="italic opacity-70">{t("retentionSettings.deletionReasonCatalogEmpty")}</p>
            ) : (
              <ul>
                {state.deletionReasonCatalog.map((entry, index) => (
                  <li key={entry}>
                    {entry}
                    <button
                      type="button"
                      onClick={() =>
                        setState((prev) => ({
                          ...prev,
                          deletionReasonCatalog: prev.deletionReasonCatalog.filter(
                            (_, i) => i !== index
                          ),
                        }))
                      }
                      className={secondaryBtn}
                    >
                      {t("common.delete")}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="deletion-reason-catalog-add">
              <input
                value={state.newReasonInput}
                onChange={(e) => setState((prev) => ({ ...prev, newReasonInput: e.target.value }))}
                placeholder={t("retentionSettings.deletionReasonCatalogAddPlaceholder")}
                className={fieldInput}
              />
              <button
                type="button"
                disabled={
                  !state.newReasonInput.trim() ||
                  state.deletionReasonCatalog.includes(state.newReasonInput.trim())
                }
                onClick={() =>
                  setState((prev) => ({
                    ...prev,
                    deletionReasonCatalog: [...prev.deletionReasonCatalog, prev.newReasonInput.trim()],
                    newReasonInput: "",
                  }))
                }
                className={secondaryBtn}
              >
                {t("retentionSettings.deletionReasonCatalogAdd")}
              </button>
            </div>
          </div>

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
              className={fieldInput}
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
              className={fieldInput}
            />
          </label>
          <div className="flex gap-2">
            <button type="submit" disabled={state.isSaving} className={primaryBtn}>
              {t("common.save")}
            </button>
          </div>
        </form>
      )}

      {state.error && (
        <p className="text-danger" role="alert">
          {state.error}
        </p>
      )}
      {state.savedAt !== null && !state.error && (
        <p className="text-sm opacity-80">{t("retentionSettings.saved")}</p>
      )}
      {state.retentionConfig && state.trashConfig && (
        <p className="text-sm opacity-80">
          {t("retentionSettings.updatedAt")}:{" "}
          {new Date(state.retentionConfig.updated_at).toLocaleString()}
        </p>
      )}
    </div>
  );
}

// The two deletion-reason catalogues (P98-S3): document- and folder-service
// keep separate retention settings on purpose (ADR 0233), but a difference in
// the catalogue is usually unintended - in the dev stack documents had none
// while folders had four. Shown here with an offer to align both to their
// union, so nothing is lost.
function ReasonCatalogComparison({
  token,
  refreshKey,
  onAligned,
}: {
  token: string;
  refreshKey: number;
  onAligned: () => void;
}) {
  const { t } = useI18n();
  const [documents, setDocuments] = useState<RetentionConfig | null>(null);
  const [folders, setFolders] = useState<RetentionConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [aligned, setAligned] = useState(false);

  useEffect(() => {
    Promise.all([getRetentionConfig(token), getFolderRetentionConfig(token)])
      .then(([documentConfig, folderConfig]) => {
        setDocuments(documentConfig);
        setFolders(folderConfig);
      })
      .catch(() => {
        // A section that can't load says so itself.
        setDocuments(null);
        setFolders(null);
      });
  }, [token, refreshKey]);

  if (!documents || !folders) return null;
  const onlyDocuments = documents.deletion_reason_catalog.filter(
    (r) => !folders.deletion_reason_catalog.includes(r)
  );
  const onlyFolders = folders.deletion_reason_catalog.filter(
    (r) => !documents.deletion_reason_catalog.includes(r)
  );
  if (onlyDocuments.length === 0 && onlyFolders.length === 0) {
    return aligned ? (
      <p role="status" className="text-sm">
        {t("retentionSettings.catalogAligned")}
      </p>
    ) : null;
  }

  async function align() {
    if (!documents || !folders) return;
    setBusy(true);
    setError(null);
    const union = [...documents.deletion_reason_catalog, ...onlyFolders];
    try {
      await Promise.all([
        updateRetentionConfig(token, {
          deletionReasonRequired: documents.deletion_reason_required,
          reminderLeadDays: documents.reminder_lead_days,
          deletionReasonCatalog: union,
        }),
        updateFolderRetentionConfig(token, {
          deletionReasonRequired: folders.deletion_reason_required,
          reminderLeadDays: folders.reminder_lead_days,
          deletionReasonCatalog: union,
        }),
      ]);
      setAligned(true);
      onAligned();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.actionError"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="mb-6 rounded-lg border border-border bg-hover-bg p-4"
      role="region"
      aria-label={t("retentionSettings.catalogDiffHeading")}
    >
      <p className="m-0 font-semibold">{t("retentionSettings.catalogDiffHeading")}</p>
      {onlyDocuments.length > 0 && (
        <p className="mb-0 text-sm">
          {t("retentionSettings.catalogOnlyDocuments", { reasons: onlyDocuments.join(", ") })}
        </p>
      )}
      {onlyFolders.length > 0 && (
        <p className="mb-0 text-sm">
          {t("retentionSettings.catalogOnlyFolders", { reasons: onlyFolders.join(", ") })}
        </p>
      )}
      <p className="text-sm opacity-80">{t("retentionSettings.catalogDiffHint")}</p>
      <button
        type="button"
        onClick={align}
        disabled={busy}
        className="rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap"
      >
        {t("retentionSettings.catalogAlign")}
      </button>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export function RetentionSettings() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  // Bumped after a section saves (the comparison reloads) and after aligning
  // (both sections reload with the new catalogue).
  const [comparisonKey, setComparisonKey] = useState(0);
  const [sectionsKey, setSectionsKey] = useState(0);

  if (!accessToken) return null;

  return (
    <>
      <p className="text-sm opacity-80">{t("retentionSettings.hint")}</p>
      <ReasonCatalogComparison
        token={accessToken}
        refreshKey={comparisonKey}
        onAligned={() => {
          setSectionsKey((k) => k + 1);
          setComparisonKey((k) => k + 1);
        }}
      />
      <RetentionSection
        key={`documents-${sectionsKey}`}
        onSaved={() => setComparisonKey((k) => k + 1)}
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
              deletionReasonCatalog: payload.deletionReasonCatalog,
            }),
            updateTrashConfig(accessToken, { restorePeriodDays: payload.restorePeriodDays }),
          ]);
          return { retention, trash };
        }}
      />
      <RetentionSection
        key={`folders-${sectionsKey}`}
        onSaved={() => setComparisonKey((k) => k + 1)}
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
              deletionReasonCatalog: payload.deletionReasonCatalog,
            }),
            updateFolderTrashConfig(accessToken, { restorePeriodDays: payload.restorePeriodDays }),
          ]);
          return { retention, trash };
        }}
      />
    </>
  );
}
