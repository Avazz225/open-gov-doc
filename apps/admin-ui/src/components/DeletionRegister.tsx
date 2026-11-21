"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  listCaseDeletionRegister,
  listDeletionRegister,
  listFolderDeletionRegister,
  type CaseDeletionRegisterEntry,
  type DeletionRegisterEntry,
  type FolderDeletionRegisterEntry,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { PrincipalName } from "./PrincipalName";

type Trigger =
  | "forced_deletion"
  | "trash_expiry"
  | "manual_purge"
  | "retention_tombstone"
  | "retention_deleted"
  | "restore_reconciliation";

interface MergedEntry {
  key: string;
  type: "document" | "folder" | "case";
  objectId: string;
  // Cases (P89-S2): the Vorgangsnummer is what people recognize.
  label?: string | null;
  trigger: Trigger;
  reason: string | null;
  // Cases: how many document references went with the case.
  referenceCount?: number;
  triggeredBy: string | null;
  occurredAt: string;
}

const TRIGGER_KEYS: Record<Trigger, string> = {
  forced_deletion: "deletionRegister.triggerForcedDeletion",
  trash_expiry: "deletionRegister.triggerTrashExpiry",
  manual_purge: "deletionRegister.triggerManualPurge",
  retention_tombstone: "deletionRegister.triggerRetentionTombstone",
  retention_deleted: "deletionRegister.triggerRetentionDeleted",
  restore_reconciliation: "deletionRegister.triggerRestoreReconciliation",
};

const TYPE_KEYS = {
  document: "deletionRegister.typeDocument",
  folder: "deletionRegister.typeFolder",
  case: "deletionRegister.typeCase",
} as const;

export function mergeEntries(
  documents: DeletionRegisterEntry[],
  folders: FolderDeletionRegisterEntry[],
  cases: CaseDeletionRegisterEntry[] = []
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
    ...cases.map((entry) => ({
      key: `case-${entry.id}`,
      type: "case" as const,
      objectId: entry.case_id,
      label: entry.vorgangsnummer,
      trigger:
        entry.trigger === "restore_reconciliation"
          ? ("restore_reconciliation" as const)
          : entry.mode === "deleted"
            ? ("retention_deleted" as const)
            : ("retention_tombstone" as const),
      reason: entry.reason,
      referenceCount: entry.document_reference_count,
      triggeredBy: entry.triggered_by,
      occurredAt: entry.occurred_at,
    })),
  ];
  return merged.sort((a, b) => b.occurredAt.localeCompare(a.occurredAt));
}

// Deletion register (5.2a, since P7-S1, extended with folders since P7-S1b)
// - a pure read-only table that merges the two independent registers from
// document-service and folder-service for display purposes (see
// docs/services/document-service.md regarding the deliberately still
// missing separate backup policy, phase 11). Since P89-S2 also case-service's
// register of destroyed case binders; if only that one is unreachable, the
// documents and folders are still shown, with a note.
export function DeletionRegister() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [entries, setEntries] = useState<MergedEntry[]>([]);
  const [unreachable, setUnreachable] = useState(false);
  const [casesUnreachable, setCasesUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      const [documents, folders, cases] = await Promise.all([
        listDeletionRegister(accessToken),
        listFolderDeletionRegister(accessToken),
        listCaseDeletionRegister(accessToken).catch(() => null),
      ]);
      setCasesUnreachable(cases === null);
      setEntries(mergeEntries(documents, folders, cases ?? []));
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
  if (unreachable) return <p className="italic opacity-70">{t("deletionRegister.unreachable")}</p>;

  return (
    <div className="rounded-lg border border-border p-4 mb-6">
      <p className="text-sm opacity-80">{t("deletionRegister.hint")}</p>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
      {casesUnreachable && (
        <p className="text-sm italic opacity-70">{t("deletionRegister.caseUnreachable")}</p>
      )}
      {entries.length === 0 ? (
        <p className="italic opacity-70">{t("deletionRegister.empty")}</p>
      ) : (
        <div className="dms-scroll-x">
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
                  <td>{t(TYPE_KEYS[entry.type])}</td>
                  <td>
                    {entry.label ? (
                      <>
                        {entry.label}
                        <span className="block font-mono text-xs opacity-70">{entry.objectId}</span>
                      </>
                    ) : (
                      entry.objectId
                    )}
                  </td>
                  <td>
                    {/* An unknown trigger from a newer backend shows as is. */}
                    {TRIGGER_KEYS[entry.trigger] ? t(TRIGGER_KEYS[entry.trigger]) : entry.trigger}
                    {entry.referenceCount !== undefined && (
                      <span className="block text-xs opacity-70">
                        {t("deletionRegister.caseReferences", { count: entry.referenceCount })}
                      </span>
                    )}
                  </td>
                  <td>{entry.reason ?? "—"}</td>
                  <td>
                    <PrincipalName value={entry.triggeredBy} />
                  </td>
                  <td className="whitespace-nowrap">{new Date(entry.occurredAt).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
