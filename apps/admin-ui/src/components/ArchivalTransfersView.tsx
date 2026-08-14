"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  downloadCaseArchivalPackage,
  getCaseArchivalConfig,
  listArchivalTransfers,
  listCaseArchivalTransfers,
  requestDocumentArchive,
  retrieveArchivalTransfer,
  retryArchivalTransfer,
  retryCaseArchivalTransfer,
  updateCaseArchivalConfig,
  type ArchivalTransfer,
  type CaseArchivalTransfer,
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

const STATUS_OPTIONS = [
  "pending",
  "locked",
  "copied",
  "verified",
  "released",
  "dehydrated",
  "failed",
  "failed_permanent",
] as const;

// Nur Status, deren Archivkopie bereits geschrieben und verifiziert wurde,
// sind rückholbar (5.6) - "pending"/"locked"/"copied"/"verified"/"failed"
// haben (noch) keine verlässliche Archivkopie.
const RETRIEVABLE_STATUSES = new Set(["released", "dehydrated"]);

// "failed_permanent" -> "FailedPermanent" - Statuswerte mit Unterstrich
// (seit Post-Roadmap Phase 20 Session 2/7) brauchen PascalCase je Wortteil,
// nicht nur eine Großschreibung des ersten Buchstabens.
function toPascalCase(value: string): string {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join("");
}

function statusLabel(t: (key: string) => string, status: string): string {
  const key = `archivalTransfers.status${toPascalCase(status)}`;
  const label = t(key);
  return label === key ? status : label;
}

const CASE_STATUS_OPTIONS = [
  "pending",
  "locked",
  "packaged",
  "verified",
  "released",
  "failed",
  "failed_permanent",
] as const;

function caseStatusLabel(t: (key: string) => string, status: string): string {
  const key = `archivalTransfers.caseStatus${toPascalCase(status)}`;
  const label = t(key);
  return label === key ? status : label;
}

// Aussonderung & Langzeitarchivierung (5.6, seit P7-S3) - reine
// Status-/Rückhol-Ansicht auf die vom archival-service geführte
// Transfer-Zustandsmaschine. Auslösung selbst (Objekttyp-Frist/manueller
// Trigger) passiert in document-service, hier nur Beobachtung + Rückholung.
export function ArchivalTransfersView() {
  return (
    <div>
      <DocumentArchivalSection />
      <CaseArchivalSection />
    </div>
  );
}

function DocumentArchivalSection() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [transfers, setTransfers] = useState<ArchivalTransfer[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [retrievingId, setRetrievingId] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [requestDocumentId, setRequestDocumentId] = useState("");
  const [isRequestingArchive, setIsRequestingArchive] = useState(false);
  const [requestSuccess, setRequestSuccess] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      setTransfers(await listArchivalTransfers(accessToken, statusFilter || undefined));
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setUnreachable(true);
      }
    } finally {
      setIsLoading(false);
    }
  }, [accessToken, statusFilter]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleRetrieve(transfer: ArchivalTransfer) {
    if (!accessToken) return;
    if (!window.confirm(t("archivalTransfers.confirmRetrieve", { documentId: transfer.document_id })))
      return;
    setError(null);
    setRetrievingId(transfer.id);
    try {
      await retrieveArchivalTransfer(accessToken, transfer.id);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("archivalTransfers.retrieveError"));
    } finally {
      setRetrievingId(null);
    }
  }

  async function handleRetry(transfer: ArchivalTransfer) {
    if (!accessToken) return;
    setError(null);
    setRetryingId(transfer.id);
    try {
      await retryArchivalTransfer(accessToken, transfer.id);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("archivalTransfers.retryError"));
    } finally {
      setRetryingId(null);
    }
  }

  async function handleRequestArchive(event: FormEvent) {
    event.preventDefault();
    if (!accessToken || !requestDocumentId.trim()) return;
    setRequestError(null);
    setRequestSuccess(false);
    setIsRequestingArchive(true);
    try {
      await requestDocumentArchive(accessToken, requestDocumentId.trim());
      setRequestSuccess(true);
      setRequestDocumentId("");
    } catch (err) {
      setRequestError(
        err instanceof ApiError ? err.message : t("archivalTransfers.requestArchiveError")
      );
    } finally {
      setIsRequestingArchive(false);
    }
  }

  if (isLoading) return <p>{t("common.loading")}</p>;
  if (unreachable) return <p className="empty-state">{t("archivalTransfers.unreachable")}</p>;

  return (
    <div className="card">
      <p className="hint">{t("archivalTransfers.hint")}</p>

      <form
        onSubmit={handleRequestArchive}
        aria-label={t("archivalTransfers.requestArchiveFormLabel")}
        className="form-grid"
      >
        <label>
          {t("archivalTransfers.requestArchiveLabel")}
          <input
            type="text"
            value={requestDocumentId}
            onChange={(e) => {
              setRequestDocumentId(e.target.value);
              setRequestSuccess(false);
              setRequestError(null);
            }}
            placeholder={t("archivalTransfers.requestArchivePlaceholder")}
          />
        </label>
        <div className="actions">
          <button type="submit" disabled={isRequestingArchive || !requestDocumentId.trim()}>
            {isRequestingArchive ? t("common.loading") : t("archivalTransfers.requestArchiveButton")}
          </button>
        </div>
      </form>
      {requestError && (
        <p className="error-text" role="alert">
          {requestError}
        </p>
      )}
      {requestSuccess && !requestError && (
        <p className="hint">{t("archivalTransfers.requestArchiveSuccess")}</p>
      )}

      <label>
        {t("archivalTransfers.filterStatus")}
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">{t("archivalTransfers.statusAll")}</option>
          {STATUS_OPTIONS.map((status) => (
            <option key={status} value={status}>
              {statusLabel(t, status)}
            </option>
          ))}
        </select>
      </label>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {transfers.length === 0 ? (
        <p className="empty-state">{t("archivalTransfers.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("archivalTransfers.documentId")}</th>
              <th>{t("archivalTransfers.status")}</th>
              <th>{t("archivalTransfers.archiveFormat")}</th>
              <th>{t("archivalTransfers.encrypted")}</th>
              <th>{t("archivalTransfers.releasedAt")}</th>
              <th>{t("archivalTransfers.dehydratedAt")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {transfers.map((transfer) => (
              <tr key={transfer.id}>
                <td>{transfer.document_id}</td>
                <td>
                  <span
                    className={`badge ${transfer.status === "failed" || transfer.status === "failed_permanent" ? "down" : "ok"}`}
                  >
                    {statusLabel(t, transfer.status)}
                  </span>
                  {(transfer.status === "failed" || transfer.status === "failed_permanent") &&
                    transfer.error_message && (
                      <p className="hint">
                        {t("archivalTransfers.errorMessage")}: {transfer.error_message}
                      </p>
                    )}
                </td>
                <td>{transfer.archive_format ?? "—"}</td>
                <td>{transfer.encrypted ? "✓" : "—"}</td>
                <td>{transfer.released_at ? new Date(transfer.released_at).toLocaleString() : "—"}</td>
                <td>
                  {transfer.dehydrated_at ? new Date(transfer.dehydrated_at).toLocaleString() : "—"}
                </td>
                <td>
                  {RETRIEVABLE_STATUSES.has(transfer.status) && (
                    <button
                      type="button"
                      onClick={() => handleRetrieve(transfer)}
                      disabled={retrievingId !== null}
                    >
                      {retrievingId === transfer.id
                        ? t("common.loading")
                        : t("archivalTransfers.retrieve")}
                    </button>
                  )}
                  {transfer.status === "failed_permanent" && (
                    <button
                      type="button"
                      onClick={() => handleRetry(transfer)}
                      disabled={retryingId !== null}
                    >
                      {retryingId === transfer.id ? t("common.loading") : t("archivalTransfers.retry")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// XDOMEA-Aussonderung für Umlaufmappen (5.6, seit P7-S3b) - eigener
// Abschnitt statt eines neuen Seiten-Slugs, gleiches Mehr-Sektionen-Muster
// wie RetentionSettings (Dokumente + Ordner in einem Formular). Installations-
// weite Konfiguration (kein Pendant zu ObjectType.default_archive_after_days,
// da Umlaufmappen keine eigene applies_to-Kategorie haben) + Statustabelle
// mit reinem Download (kein Zurückschreiben - eine Umlaufmappe besitzt
// keinen eigenen Live-Speicherplatz).
function CaseArchivalSection() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [transfers, setTransfers] = useState<CaseArchivalTransfer[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);

  const [defaultDays, setDefaultDays] = useState("");
  const [encryptionEnabled, setEncryptionEnabled] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      const [loadedTransfers, config] = await Promise.all([
        listCaseArchivalTransfers(accessToken, statusFilter || undefined),
        getCaseArchivalConfig(accessToken),
      ]);
      setTransfers(loadedTransfers);
      setDefaultDays(
        config.default_archive_after_days_closed === null
          ? ""
          : String(config.default_archive_after_days_closed)
      );
      setEncryptionEnabled(config.archive_encryption_enabled);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setUnreachable(true);
      }
    } finally {
      setIsLoading(false);
    }
  }, [accessToken, statusFilter]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleSaveConfig(event: FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    setConfigError(null);
    setSavedAt(null);
    setIsSaving(true);
    try {
      await updateCaseArchivalConfig(
        accessToken,
        defaultDays.trim() === "" ? null : Number(defaultDays),
        encryptionEnabled
      );
      setSavedAt(Date.now());
    } catch (err) {
      setConfigError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setIsSaving(false);
    }
  }

  async function handleDownload(transfer: CaseArchivalTransfer) {
    if (!accessToken) return;
    setError(null);
    setDownloadingId(transfer.id);
    try {
      const blob = await downloadCaseArchivalPackage(accessToken, transfer.id);
      triggerBrowserDownload(blob, `aussonderung-${transfer.case_id}.zip`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("archivalTransfers.downloadError"));
    } finally {
      setDownloadingId(null);
    }
  }

  async function handleRetry(transfer: CaseArchivalTransfer) {
    if (!accessToken) return;
    setError(null);
    setRetryingId(transfer.id);
    try {
      await retryCaseArchivalTransfer(accessToken, transfer.id);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("archivalTransfers.retryError"));
    } finally {
      setRetryingId(null);
    }
  }

  if (isLoading) return <p>{t("common.loading")}</p>;
  if (unreachable) return <p className="empty-state">{t("archivalTransfers.caseUnreachable")}</p>;

  return (
    <div className="card">
      <h2>{t("archivalTransfers.caseHeading")}</h2>
      <p className="hint">{t("archivalTransfers.caseHint")}</p>

      <form onSubmit={handleSaveConfig} aria-label={t("archivalTransfers.caseConfigFormLabel")}>
        <div className="form-grid">
          <label>
            {t("archivalTransfers.caseDefaultDaysLabel")}
            <input
              type="number"
              min="0"
              value={defaultDays}
              onChange={(e) => setDefaultDays(e.target.value)}
              placeholder={t("archivalTransfers.caseDefaultDaysPlaceholder")}
            />
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={encryptionEnabled}
              onChange={(e) => setEncryptionEnabled(e.target.checked)}
            />
            {t("archivalTransfers.caseEncryptionEnabledLabel")}
          </label>
        </div>
        <div className="actions">
          <button type="submit" disabled={isSaving}>
            {t("common.save")}
          </button>
        </div>
      </form>
      {configError && (
        <p className="error-text" role="alert">
          {configError}
        </p>
      )}
      {savedAt !== null && !configError && <p className="hint">{t("archivalTransfers.caseConfigSaved")}</p>}

      <label>
        {t("archivalTransfers.filterStatus")}
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">{t("archivalTransfers.statusAll")}</option>
          {CASE_STATUS_OPTIONS.map((status) => (
            <option key={status} value={status}>
              {caseStatusLabel(t, status)}
            </option>
          ))}
        </select>
      </label>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {transfers.length === 0 ? (
        <p className="empty-state">{t("archivalTransfers.caseEmpty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("archivalTransfers.caseId")}</th>
              <th>{t("archivalTransfers.status")}</th>
              <th>{t("archivalTransfers.encrypted")}</th>
              <th>{t("archivalTransfers.releasedAt")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {transfers.map((transfer) => (
              <tr key={transfer.id}>
                <td>{transfer.case_id}</td>
                <td>
                  <span
                    className={`badge ${transfer.status === "failed" || transfer.status === "failed_permanent" ? "down" : "ok"}`}
                  >
                    {caseStatusLabel(t, transfer.status)}
                  </span>
                  {(transfer.status === "failed" || transfer.status === "failed_permanent") &&
                    transfer.error_message && (
                      <p className="hint">
                        {t("archivalTransfers.errorMessage")}: {transfer.error_message}
                      </p>
                    )}
                </td>
                <td>{transfer.encrypted ? "✓" : "—"}</td>
                <td>{transfer.released_at ? new Date(transfer.released_at).toLocaleString() : "—"}</td>
                <td>
                  {transfer.status === "released" && (
                    <button
                      type="button"
                      onClick={() => handleDownload(transfer)}
                      disabled={downloadingId !== null}
                    >
                      {downloadingId === transfer.id
                        ? t("common.loading")
                        : t("archivalTransfers.caseDownload")}
                    </button>
                  )}
                  {transfer.status === "failed_permanent" && (
                    <button
                      type="button"
                      onClick={() => handleRetry(transfer)}
                      disabled={retryingId !== null}
                    >
                      {retryingId === transfer.id ? t("common.loading") : t("archivalTransfers.retry")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
