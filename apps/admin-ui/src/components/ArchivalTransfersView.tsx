"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import { ApiError, listArchivalTransfers, retrieveArchivalTransfer, type ArchivalTransfer } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const STATUS_OPTIONS = [
  "pending",
  "locked",
  "copied",
  "verified",
  "released",
  "dehydrated",
  "failed",
] as const;

// Nur Status, deren Archivkopie bereits geschrieben und verifiziert wurde,
// sind rückholbar (5.6) - "pending"/"locked"/"copied"/"verified"/"failed"
// haben (noch) keine verlässliche Archivkopie.
const RETRIEVABLE_STATUSES = new Set(["released", "dehydrated"]);

function statusLabel(t: (key: string) => string, status: string): string {
  const key = `archivalTransfers.status${status.charAt(0).toUpperCase()}${status.slice(1)}`;
  const label = t(key);
  return label === key ? status : label;
}

// Aussonderung & Langzeitarchivierung (5.6, seit P7-S3) - reine
// Status-/Rückhol-Ansicht auf die vom archival-service geführte
// Transfer-Zustandsmaschine. Auslösung selbst (Objekttyp-Frist/manueller
// Trigger) passiert in document-service, hier nur Beobachtung + Rückholung.
export function ArchivalTransfersView() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [transfers, setTransfers] = useState<ArchivalTransfer[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [retrievingId, setRetrievingId] = useState<string | null>(null);

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

  if (isLoading) return <p>{t("common.loading")}</p>;
  if (unreachable) return <p className="empty-state">{t("archivalTransfers.unreachable")}</p>;

  return (
    <div className="card">
      <p className="hint">{t("archivalTransfers.hint")}</p>

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
                  <span className={`badge ${transfer.status === "failed" ? "down" : "ok"}`}>
                    {statusLabel(t, transfer.status)}
                  </span>
                  {transfer.status === "failed" && transfer.error_message && (
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
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
