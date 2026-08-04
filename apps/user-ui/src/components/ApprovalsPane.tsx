"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  approveApprovalRequest,
  listApprovalRequests,
  rejectApprovalRequest,
  type ApprovalRequest,
} from "@/lib/api";

function objectId(request: ApprovalRequest): string {
  return String(request.payload.document_id ?? request.payload.folder_id ?? "?");
}

// Minimale Genehmigungs-Inbox für den Löschantrag-Workflow (5.2, seit
// P7-S1c) - bewusst nur für `document.delete`/`folder.delete` gefiltert,
// keine generische Alle-Aktionstypen-Inbox (das wäre der später geplante
// "Administrative Papierkorb", siehe PROGRESS.md). Lebt in der User-UI statt
// der Admin-UI, da sich hier reguläre Nutzer gegenseitig genehmigen, kein
// administrativer Vorgang.
export function ApprovalsPane({
  token,
  currentUsername,
}: {
  token: string;
  currentUsername: string;
}) {
  const { t } = useI18n();
  const [requests, setRequests] = useState<ApprovalRequest[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!token) return;
    setIsLoading(true);
    setError(null);
    try {
      const [documentRequests, folderRequests] = await Promise.all([
        listApprovalRequests(token, { actionType: "document.delete", status: "pending" }),
        listApprovalRequests(token, { actionType: "folder.delete", status: "pending" }),
      ]);
      setRequests(
        [...documentRequests, ...folderRequests].sort((a, b) =>
          b.created_at.localeCompare(a.created_at)
        )
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("approvals.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [token, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleApprove(request: ApprovalRequest) {
    setBusyId(request.id);
    setError(null);
    try {
      await approveApprovalRequest(token, request.id, currentUsername);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("approvals.decisionError"));
    } finally {
      setBusyId(null);
    }
  }

  async function handleReject(request: ApprovalRequest) {
    setBusyId(request.id);
    setError(null);
    try {
      await rejectApprovalRequest(token, request.id, currentUsername);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("approvals.decisionError"));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="approvals-pane" aria-label={t("approvals.paneLabel")}>
      <h2 className="pane-heading">{t("approvals.heading")}</h2>
      <p className="hint">{t("approvals.hint")}</p>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : requests.length === 0 ? (
        <p className="empty-state">{t("approvals.empty")}</p>
      ) : (
        <ul className="entry-list">
          {requests.map((request) => {
            const isOwnRequest = request.initiated_by === currentUsername;
            return (
              <li className="entry-row" key={request.id}>
                <span className="entry-name">
                  {request.action_type === "document.delete"
                    ? t("approvals.typeDocument")
                    : t("approvals.typeFolder")}{" "}
                  {objectId(request)} — {t("approvals.requestedBy", { name: request.initiated_by })}
                </span>
                <span className="actions">
                  <button
                    type="button"
                    onClick={() => handleApprove(request)}
                    disabled={busyId === request.id || isOwnRequest}
                    title={isOwnRequest ? t("approvals.cannotApproveOwn") : undefined}
                  >
                    {t("approvals.approve")}
                  </button>
                  <button
                    type="button"
                    onClick={() => handleReject(request)}
                    disabled={busyId === request.id}
                  >
                    {t("approvals.reject")}
                  </button>
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
