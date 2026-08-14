"use client";

import { Fragment, useEffect, useState, type FormEvent } from "react";
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

// Generic four-eyes approval inbox (4.3, 8, P14-S2) - consumes
// `permission-service`'s `GET /approval-requests` unfiltered by
// `action_type` (previously there were only narrowly filtered individual
// consumers in admin-ui/user-ui, see docs/services/reviewer-ui.md
// "Relationship to existing consumers"). `payload` deliberately stays raw
// JSON in the detail view - this app does not know the domain meaning of the
// individual `action_type`s (and should not need to, otherwise it would have
// to be adapted for every new action type added to the system).
export function ApprovalList() {
  const { accessToken, user } = useAuth();
  const { t } = useI18n();

  const [statusFilter, setStatusFilter] = useState<StatusFilter>("pending");
  const [requests, setRequests] = useState<ApprovalRequest[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  // Post-roadmap Phase 22 Session 4: replaced `window.prompt` with an inline
  // form (no native browser popup, consistent with the rest of this app's
  // form style, testable with Testing Library).
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");

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

  function handleStartReject(requestId: string) {
    setActionError(null);
    setRejectReason("");
    setRejectingId(requestId);
  }

  function handleCancelReject() {
    setRejectingId(null);
    setRejectReason("");
  }

  async function handleConfirmReject(event: FormEvent, request: ApprovalRequest) {
    event.preventDefault();
    if (!accessToken || !user) return;
    setActionError(null);
    setBusyId(request.id);
    try {
      await rejectRequest(accessToken, request.id, {
        rejectedBy: user.username,
        reason: rejectReason.trim() || undefined,
      });
      setRejectingId(null);
      setRejectReason("");
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
                      {request.status === "pending" && rejectingId !== request.id && (
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
                            onClick={() => handleStartReject(request.id)}
                          >
                            {t("approvalList.rejectButton")}
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
                {rejectingId === request.id && (
                  <tr className="detail-row">
                    <td colSpan={5}>
                      <form
                        aria-label={t("approvalList.rejectFormLabel")}
                        className="form-grid"
                        onSubmit={(e) => handleConfirmReject(e, request)}
                      >
                        <label>
                          {t("approvalList.rejectReasonLabel")}
                          <input
                            value={rejectReason}
                            onChange={(e) => setRejectReason(e.target.value)}
                            placeholder={t("approvalList.rejectReasonPlaceholder")}
                          />
                        </label>
                        <div className="actions">
                          <button type="submit" disabled={busyId === request.id}>
                            {t("approvalList.rejectConfirm")}
                          </button>
                          <button type="button" onClick={handleCancelReject}>
                            {t("approvalList.rejectCancel")}
                          </button>
                        </div>
                      </form>
                    </td>
                  </tr>
                )}
                {expandedId === request.id && (
                  <tr className="detail-row">
                    <td colSpan={5}>
                      <pre>{JSON.stringify(request.payload, null, 2)}</pre>
                      {request.reason && (
                        <p>
                          <strong>{t("approvalList.rejectReasonDisplayLabel")}</strong> {request.reason}
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
