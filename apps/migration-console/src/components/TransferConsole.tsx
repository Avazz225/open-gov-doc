"use client";

import { Fragment, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  createTransfer,
  listPairedInstallations,
  listTransfers,
  type PairedInstallation,
  type Transfer,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const TERMINAL_SUCCESS = new Set(["released", "deleted", "dry_run_completed"]);
const POLL_INTERVAL_MS = 5_000;

function statusBadgeClass(status: string): string {
  if (status === "failed") return "badge badge-rejected";
  if (TERMINAL_SUCCESS.has(status)) return "badge badge-approved";
  return "badge badge-pending";
}

const PHASE_FIELDS: { key: keyof Transfer; labelKey: string }[] = [
  { key: "locked_at", labelKey: "transfers.phaseLocked" },
  { key: "copied_at", labelKey: "transfers.phaseCopied" },
  { key: "verified_at", labelKey: "transfers.phaseVerified" },
  { key: "released_at", labelKey: "transfers.phaseReleased" },
  { key: "deletion_scheduled_at", labelKey: "transfers.phaseDeletionScheduled" },
  { key: "deleted_at", labelKey: "transfers.phaseDeleted" },
];

// Console for transfer operations (7.2, concept 8) - `POST /transfers`,
// `.../steps/*` (target of automatic `connector_call` service tasks) and the
// `/inbound/*` endpoints are deliberately NOT part of this UI (internal
// implementation details of the workflow, see
// docs/services/migration-console.md "Deliberate Boundaries").
export function TransferConsole() {
  const { accessToken, user } = useAuth();
  const { t } = useI18n();

  const [installations, setInstallations] = useState<PairedInstallation[]>([]);
  const [transfers, setTransfers] = useState<Transfer[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [sourceFolderId, setSourceFolderId] = useState("");
  const [targetInstallationId, setTargetInstallationId] = useState("");
  const [createdBy, setCreatedBy] = useState("");
  const [dryRun, setDryRun] = useState(false);
  const [retentionDays, setRetentionDays] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const reloadTransfers = () => {
    if (!accessToken) return;
    listTransfers(accessToken, statusFilter || undefined)
      .then(setTransfers)
      .catch(() => setError(t("common.loadError")));
  };

  useEffect(reloadTransfers, [accessToken, statusFilter, t]);

  useEffect(() => {
    if (!accessToken) return;
    listPairedInstallations(accessToken)
      .then(setInstallations)
      .catch(() => setError(t("common.loadError")));
  }, [accessToken, t]);

  // Lightweight polling instead of push - a transfer automatically goes
  // through several phases in the background (workflow-service instance);
  // without reloading, the console would stay stuck at the state of the
  // last page load (same pattern as `MaintenanceBanner`'s 30s poll).
  useEffect(() => {
    const interval = setInterval(reloadTransfers, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken, statusFilter]);

  useEffect(() => {
    if (user && !createdBy) setCreatedBy(user.username);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  function installationName(id: string): string {
    return installations.find((i) => i.id === id)?.display_name ?? id;
  }

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    setFormError(null);
    setNotice(null);
    setSubmitting(true);
    try {
      const result = await createTransfer(accessToken, {
        sourceFolderId,
        targetInstallationId,
        createdBy,
        dryRun,
        retentionDays: retentionDays ? Number(retentionDays) : undefined,
      });
      if (result.status === "pending_approval") {
        setNotice(t("transfers.pendingApprovalNotice", { id: result.approval_request_id ?? "" }));
      }
      setSourceFolderId("");
      setDryRun(false);
      setRetentionDays("");
      setShowForm(false);
      reloadTransfers();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : t("transfers.startError"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section>
      <div className="top-bar">
        <h1>{t("transfers.heading")}</h1>
        <button type="button" onClick={() => setShowForm((v) => !v)}>
          {t("transfers.newButton")}
        </button>
      </div>
      <p className="hint">{t("transfers.hint")}</p>

      <label htmlFor="status-filter">{t("transfers.statusFilterLabel")}</label>{" "}
      <select
        id="status-filter"
        value={statusFilter}
        onChange={(e) => setStatusFilter(e.target.value)}
      >
        <option value="">{t("transfers.statusAll")}</option>
        {[
          "pending",
          "locked",
          "copied",
          "verified",
          "released",
          "deletion_scheduled",
          "deleted",
          "dry_run_completed",
          "failed",
        ].map((status) => (
          <option key={status} value={status}>
            {status}
          </option>
        ))}
      </select>

      {notice && <p className="success-text">{notice}</p>}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {showForm && (
        <form className="inline-form" onSubmit={handleCreate}>
          <label htmlFor="source-folder-id">{t("transfers.sourceFolderIdLabel")}</label>
          <input
            id="source-folder-id"
            value={sourceFolderId}
            onChange={(e) => setSourceFolderId(e.target.value)}
            required
          />
          <label htmlFor="target-installation">{t("transfers.targetInstallationLabel")}</label>
          <select
            id="target-installation"
            value={targetInstallationId}
            onChange={(e) => setTargetInstallationId(e.target.value)}
            required
          >
            <option value="" disabled>
              {installations.length === 0
                ? t("transfers.targetInstallationEmpty")
                : t("transfers.targetInstallationLabel")}
            </option>
            {installations.map((installation) => (
              <option key={installation.id} value={installation.id}>
                {installation.display_name}
              </option>
            ))}
          </select>
          <label htmlFor="created-by">{t("transfers.createdByLabel")}</label>
          <input
            id="created-by"
            value={createdBy}
            onChange={(e) => setCreatedBy(e.target.value)}
            required
          />
          <label>
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
            />{" "}
            {t("transfers.dryRunLabel")}
          </label>
          <label htmlFor="retention-days">{t("transfers.retentionDaysLabel")}</label>
          <input
            id="retention-days"
            type="number"
            min={0}
            value={retentionDays}
            onChange={(e) => setRetentionDays(e.target.value)}
            disabled={dryRun}
          />
          {formError && (
            <p className="error-text" role="alert">
              {formError}
            </p>
          )}
          <div className="actions">
            <button type="submit" disabled={submitting}>
              {submitting ? t("transfers.submitting") : t("transfers.submit")}
            </button>
            <button type="button" onClick={() => setShowForm(false)}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      )}

      {transfers.length === 0 ? (
        <p className="empty-state">{t("transfers.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("transfers.idColumn")}</th>
              <th>{t("transfers.sourceColumn")}</th>
              <th>{t("transfers.targetColumn")}</th>
              <th>{t("transfers.statusColumn")}</th>
              <th>{t("transfers.progressColumn")}</th>
              <th>{t("transfers.actionsColumn")}</th>
            </tr>
          </thead>
          <tbody>
            {transfers.map((transfer) => (
              <Fragment key={transfer.id}>
                <tr>
                  <td>{transfer.id.slice(0, 8)}</td>
                  <td>{transfer.source_folder_id}</td>
                  <td>{installationName(transfer.target_installation_id)}</td>
                  <td>
                    <span className={statusBadgeClass(transfer.status)}>{transfer.status}</span>
                    {transfer.dry_run && (
                      <>
                        {" "}
                        <span className="badge badge-pending">{t("transfers.dryRunBadge")}</span>
                      </>
                    )}
                  </td>
                  <td>
                    {t("transfers.documentsProgress", {
                      copied: transfer.documents_copied,
                      total: transfer.documents_total,
                      verified: transfer.documents_verified,
                    })}
                  </td>
                  <td>
                    <button
                      type="button"
                      onClick={() => setExpandedId(expandedId === transfer.id ? null : transfer.id)}
                    >
                      {t("transfers.detailButton")}
                    </button>
                  </td>
                </tr>
                {expandedId === transfer.id && (
                  <tr className="detail-row">
                    <td colSpan={6}>
                      <h2 className="hint">{t("transfers.detailHeading")}</h2>
                      {transfer.error_message && (
                        <p className="error-text">
                          <strong>{t("transfers.errorLabel")}:</strong> {transfer.error_message}
                        </p>
                      )}
                      <h3 className="hint">{t("transfers.phasesHeading")}</h3>
                      <ul>
                        {PHASE_FIELDS.map(({ key, labelKey }) => {
                          const value = transfer[key];
                          if (!value) return null;
                          return (
                            <li key={key}>
                              {t(labelKey)}: {new Date(value as string).toLocaleString("de-DE")}
                            </li>
                          );
                        })}
                      </ul>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
