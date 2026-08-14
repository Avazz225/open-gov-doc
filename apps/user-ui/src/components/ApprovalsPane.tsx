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

// Minimal approval inbox for the delete-request workflow (5.2, since
// P7-S1c) - deliberately filtered to only `document.delete`/`folder.delete`,
// not a generic all-action-types inbox (that would be the later-planned
// "administrative trash", see PROGRESS.md). Lives in the user UI instead
// of the admin UI, since here regular users approve each other's requests,
// not an administrative process.
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
