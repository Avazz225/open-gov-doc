"use client";

import { Fragment, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  approveRequest,
  listApprovalRequests,
  rejectRequest,
  type ApprovalRequest,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

type StatusFilter = "" | "pending" | "approved" | "rejected";

function statusBadgeClass(status: string): string {
  if (status === "approved") return "badge badge-approved";
  if (status === "rejected") return "badge badge-rejected";
  return "badge badge-pending";
}

// Generische Vier-Augen-Freigabe-Inbox (4.3, 8, P14-S2) - konsumiert
// `permission-service`s `GET /approval-requests` ungefiltert nach
// `action_type` (bislang gab es nur eng gefilterte Einzelkonsumenten in
// admin-ui/user-ui, siehe docs/services/reviewer-ui.md "Verhältnis zu
// bestehenden Konsumenten"). `payload` bleibt bewusst rohes JSON in der
// Detailansicht - diese App kennt die Fachbedeutung der einzelnen
// `action_type`s nicht (und soll sie auch nicht kennen müssen, sonst müsste
// sie bei jedem neuen Aktionstyp im System mit angepasst werden).
export function ApprovalList() {
  const { accessToken, user } = useAuth();
  const { t } = useI18n();

  const [statusFilter, setStatusFilter] = useState<StatusFilter>("pending");
  const [requests, setRequests] = useState<ApprovalRequest[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const reload = () => {
    if (!accessToken) return;
    listApprovalRequests(accessToken, statusFilter ? { status: statusFilter } : undefined)
      .then(setRequests)
      .catch(() => setError(t("common.loadError")));
  };

  useEffect(reload, [accessToken, statusFilter, t]);

  async function handleApprove(request: ApprovalRequest) {
    if (!accessToken || !user) return;
    if (!window.confirm(t("approvalList.approveConfirm"))) return;
    setActionError(null);
    setBusyId(request.id);
    try {
      await approveRequest(accessToken, request.id, user.username);
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : t("common.actionError"));
    } finally {
      setBusyId(null);
    }
  }

  async function handleReject(request: ApprovalRequest) {
    if (!accessToken || !user) return;
    const reason = window.prompt(t("approvalList.rejectReasonPrompt")) ?? undefined;
    setActionError(null);
    setBusyId(request.id);
    try {
      await rejectRequest(accessToken, request.id, { rejectedBy: user.username, reason });
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : t("common.actionError"));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section>
      <h1>{t("approvalList.heading")}</h1>
      <p className="hint">{t("approvalList.hint")}</p>

      <label htmlFor="status-filter">{t("approvalList.statusFilterLabel")}</label>{" "}
      <select
        id="status-filter"
        value={statusFilter}
        onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
      >
        <option value="">{t("approvalList.statusAll")}</option>
        <option value="pending">{t("approvalList.statusPending")}</option>
        <option value="approved">{t("approvalList.statusApproved")}</option>
        <option value="rejected">{t("approvalList.statusRejected")}</option>
      </select>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {actionError && (
        <p className="error-text" role="alert">
          {actionError}
        </p>
      )}

      {requests.length === 0 ? (
        <p className="empty-state">{t("approvalList.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("approvalList.actionTypeColumn")}</th>
              <th>{t("approvalList.initiatedByColumn")}</th>
              <th>{t("approvalList.createdColumn")}</th>
              <th>{t("approvalList.statusColumn")}</th>
              <th>{t("approvalList.actionsColumn")}</th>
            </tr>
          </thead>
          <tbody>
            {requests.map((request) => (
              <Fragment key={request.id}>
                <tr>
                  <td>{request.action_type}</td>
                  <td>{request.initiated_by}</td>
                  <td>{new Date(request.created_at).toLocaleString("de-DE")}</td>
                  <td>
                    <span className={statusBadgeClass(request.status)}>
                      {t(`approvalList.status${request.status[0].toUpperCase()}${request.status.slice(1)}`)}
                    </span>
                  </td>
                  <td>
                    <div className="actions">
                      <button
                        type="button"
                        onClick={() =>
                          setExpandedId(expandedId === request.id ? null : request.id)
                        }
                      >
                        {expandedId === request.id
                          ? t("approvalList.detailsToggleHide")
                          : t("approvalList.detailsToggleShow")}
                      </button>
                      {request.status === "pending" && (
                        <>
                          <button
                            type="button"
                            disabled={busyId === request.id}
                            onClick={() => handleApprove(request)}
                          >
                            {t("approvalList.approveButton")}
                          </button>
                          <button
                            type="button"
                            disabled={busyId === request.id}
                            onClick={() => handleReject(request)}
                          >
                            {t("approvalList.rejectButton")}
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
                {expandedId === request.id && (
                  <tr className="detail-row">
                    <td colSpan={5}>
                      <pre>{JSON.stringify(request.payload, null, 2)}</pre>
                      {request.reason && (
                        <p>
                          <strong>{t("approvalList.rejectReasonPrompt")}</strong> {request.reason}
                        </p>
                      )}
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
