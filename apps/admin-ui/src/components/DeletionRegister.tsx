"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import { ApiError, listDeletionRegister, type DeletionRegisterEntry } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Löschregister (5.2a, seit P7-S1) - reine Lese-Tabelle, siehe
// docs/services/document-service.md zur bewusst noch fehlenden separaten
// Backup-Politik (Phase 11).
export function DeletionRegister() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [entries, setEntries] = useState<DeletionRegisterEntry[]>([]);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      setEntries(await listDeletionRegister(accessToken));
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

  if (isLoading) return <p>{t("common.loading")}</p>;
  if (unreachable) return <p className="empty-state">{t("deletionRegister.unreachable")}</p>;

  return (
    <div className="card">
      <p className="hint">{t("deletionRegister.hint")}</p>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {entries.length === 0 ? (
        <p className="empty-state">{t("deletionRegister.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("deletionRegister.documentId")}</th>
              <th>{t("deletionRegister.trigger")}</th>
              <th>{t("deletionRegister.reason")}</th>
              <th>{t("deletionRegister.triggeredBy")}</th>
              <th>{t("deletionRegister.occurredAt")}</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.id}>
                <td>{entry.document_id}</td>
                <td>
                  {entry.trigger === "forced_deletion"
                    ? t("deletionRegister.triggerForcedDeletion")
                    : t("deletionRegister.triggerTrashExpiry")}
                </td>
                <td>{entry.reason ?? "—"}</td>
                <td>{entry.triggered_by ?? "—"}</td>
                <td>{new Date(entry.occurred_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
