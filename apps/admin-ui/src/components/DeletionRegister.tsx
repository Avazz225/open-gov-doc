"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  listDeletionRegister,
  listFolderDeletionRegister,
  type DeletionRegisterEntry,
  type FolderDeletionRegisterEntry,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

interface MergedEntry {
  key: string;
  type: "document" | "folder";
  objectId: string;
  trigger: "forced_deletion" | "trash_expiry";
  reason: string | null;
  triggeredBy: string | null;
  occurredAt: string;
}

function mergeEntries(
  documents: DeletionRegisterEntry[],
  folders: FolderDeletionRegisterEntry[]
): MergedEntry[] {
  const merged: MergedEntry[] = [
    ...documents.map((entry) => ({
      key: `document-${entry.id}`,
      type: "document" as const,
      objectId: entry.document_id,
      trigger: entry.trigger,
      reason: entry.reason,
      triggeredBy: entry.triggered_by,
      occurredAt: entry.occurred_at,
    })),
    ...folders.map((entry) => ({
      key: `folder-${entry.id}`,
      type: "folder" as const,
      objectId: entry.folder_id,
      trigger: entry.trigger,
      reason: entry.reason,
      triggeredBy: entry.triggered_by,
      occurredAt: entry.occurred_at,
    })),
  ];
  return merged.sort((a, b) => b.occurredAt.localeCompare(a.occurredAt));
}

// Löschregister (5.2a, seit P7-S1, um Ordner erweitert seit P7-S1b) - reine
// Lese-Tabelle, führt die beiden unabhängigen Register von document-service
// und folder-service anzeigeseitig zusammen (siehe docs/services/
// document-service.md zur bewusst noch fehlenden separaten Backup-Politik,
// Phase 11).
export function DeletionRegister() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [entries, setEntries] = useState<MergedEntry[]>([]);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      const [documents, folders] = await Promise.all([
        listDeletionRegister(accessToken),
        listFolderDeletionRegister(accessToken),
      ]);
      setEntries(mergeEntries(documents, folders));
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
              <th>{t("deletionRegister.type")}</th>
              <th>{t("deletionRegister.documentId")}</th>
              <th>{t("deletionRegister.trigger")}</th>
              <th>{t("deletionRegister.reason")}</th>
              <th>{t("deletionRegister.triggeredBy")}</th>
              <th>{t("deletionRegister.occurredAt")}</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.key}>
                <td>
                  {entry.type === "document"
                    ? t("deletionRegister.typeDocument")
                    : t("deletionRegister.typeFolder")}
                </td>
                <td>{entry.objectId}</td>
                <td>
                  {entry.trigger === "forced_deletion"
                    ? t("deletionRegister.triggerForcedDeletion")
                    : t("deletionRegister.triggerTrashExpiry")}
                </td>
                <td>{entry.reason ?? "—"}</td>
                <td>{entry.triggeredBy ?? "—"}</td>
                <td>{new Date(entry.occurredAt).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
