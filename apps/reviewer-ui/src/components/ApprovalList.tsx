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
import { principalName, principalTitle, usePrincipalNames } from "@/lib/usePrincipalNames";
import { actionTypeLabel } from "@/lib/display";

type StatusFilter = "" | "pending" | "approved" | "rejected";

const BADGE_BASE = "badge-hc-border inline-block rounded-full px-2 py-[0.1rem] text-xs whitespace-nowrap";

function statusBadgeClass(status: string): string {
  if (status === "approved") return `${BADGE_BASE} bg-success-bg text-success`;
  if (status === "rejected") return `${BADGE_BASE} bg-danger-bg text-danger`;
  return `${BADGE_BASE} bg-accent-bg text-accent`;
}

// The action type as a translated label (P83-S11, item 6 - S7 missed it):
// the raw type stays on hover for whoever needs the exact identifier, and an
// unknown type shows raw and monospaced instead of pretending to be prose.
function ActionType({ type }: { type: string }) {
  const { t } = useI18n();
  const { label, known } = actionTypeLabel(t, type);
  return known ? (
    <span title={type}>{label}</span>
  ) : (
    <code className="font-mono text-sm">{label}</code>
  );
}

// Generic four-eyes approval inbox (4.3, 8, P14-S2) - consumes
// `permission-service`'s `GET /approval-requests` unfiltered by
// `action_type` (previously there were only narrowly filtered individual
// consumers in admin-ui/user-ui, see docs/services/reviewer-ui.md
// "Relationship to existing consumers"). `payload` deliberately stays raw
// JSON in the detail view - this app does not know the domain meaning of the
// individual `action_type`s (and should not need to, otherwise it would have
// to be adapted for every new action type added to the system). Since P83-S11
// the known types get a translated *name* (`actionTypes.*` in i18n); a new
// type still works unadapted and simply shows its raw identifier.
export function ApprovalList() {
  const { accessToken, user } = useAuth();
  const { t, locale } = useI18n();

  const [statusFilter, setStatusFilter] = useState<StatusFilter>("pending");
  const [requests, setRequests] = useState<ApprovalRequest[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  // "Beantragt von" as a name (P86-S2): initiators are principals (ADR 0240).
  const names = usePrincipalNames(accessToken ?? "", requests.map((r) => r.initiated_by));
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
      await approveRequest(accessToken, request.id);
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

  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1 text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1 text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
  const fieldInput =
    "box-border w-full rounded-md border border-border bg-bg px-2 py-1.5 text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";
  const th = "border-b border-border px-2 py-2 text-left";
  const td = "border-b border-border px-2 py-2 text-left";

  return (
    <section>
      <h1>{t("approvalList.heading")}</h1>
      <p className="text-sm opacity-80">{t("approvalList.hint")}</p>

      <label htmlFor="status-filter" className="text-sm font-medium text-fg">
        {t("approvalList.statusFilterLabel")}
      </label>{" "}
      <select
        id="status-filter"
        value={statusFilter}
        onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
        className="rounded-md border border-border bg-bg px-1 py-0.5 text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg"
      >
        <option value="">{t("approvalList.statusAll")}</option>
        <option value="pending">{t("approvalList.statusPending")}</option>
        <option value="approved">{t("approvalList.statusApproved")}</option>
        <option value="rejected">{t("approvalList.statusRejected")}</option>
      </select>

      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
      {actionError && (
        <p className="text-danger" role="alert">
          {actionError}
        </p>
      )}

      {requests.length === 0 ? (
        <p className="italic opacity-70">{t("approvalList.empty")}</p>
      ) : (
        <div className="dms-scroll-x mb-6">
          {/* Contained scroll from 768px; below it each row stacks into a card (P83-S11, ADR 0238 Decision 4) */}
          <table className="dms-stack-table w-full border-collapse">
            <thead>
              <tr>
                <th className={th}>{t("approvalList.actionTypeColumn")}</th>
                <th className={th}>{t("approvalList.initiatedByColumn")}</th>
                <th className={th}>{t("approvalList.createdColumn")}</th>
                <th className={th}>{t("approvalList.statusColumn")}</th>
                <th className={th}>{t("approvalList.actionsColumn")}</th>
              </tr>
            </thead>
            <tbody>
              {requests.map((request) => (
                <Fragment key={request.id}>
                  <tr>
                    <td className={td} data-stack="title">
                      <ActionType type={request.action_type} />
                    </td>
                    <td
                      className={td}
                      data-label={t("approvalList.initiatedByColumn")}
                      title={principalTitle(names, request.initiated_by)}
                    >
                      {principalName(names, request.initiated_by)}
                    </td>
                    <td className={`${td} whitespace-nowrap`} data-label={t("approvalList.createdColumn")}>
                      {new Date(request.created_at).toLocaleString(locale)}
                    </td>
                    <td className={td} data-label={t("approvalList.statusColumn")}>
                      <span className={statusBadgeClass(request.status)}>
                        {t(`approvalList.status${request.status[0].toUpperCase()}${request.status.slice(1)}`)}
                      </span>
                    </td>
                    <td className={td} data-stack="actions">
                      <div className="flex gap-3">
                        <button
                          type="button"
                          onClick={() =>
                            setExpandedId(expandedId === request.id ? null : request.id)
                          }
                          className={secondaryBtn}
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
                              className={secondaryBtn}
                            >
                              {t("approvalList.approveButton")}
                            </button>
                            <button
                              type="button"
                              disabled={busyId === request.id}
                              onClick={() => handleStartReject(request.id)}
                              className={secondaryBtn}
                            >
                              {t("approvalList.rejectButton")}
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                  {rejectingId === request.id && (
                    <tr className="bg-hover-bg" data-stack="detail">
                      <td colSpan={5} className="px-2 py-2">
                        <form
                          aria-label={t("approvalList.rejectFormLabel")}
                          className="mt-2 flex max-w-[420px] flex-col gap-2 rounded-sm border border-border p-3"
                          onSubmit={(e) => handleConfirmReject(e, request)}
                        >
                          <label className="flex flex-col gap-1 text-sm font-medium text-fg">
                            {t("approvalList.rejectReasonLabel")}
                            <input
                              value={rejectReason}
                              onChange={(e) => setRejectReason(e.target.value)}
                              placeholder={t("approvalList.rejectReasonPlaceholder")}
                              className={fieldInput}
                            />
                          </label>
                          <div className="flex gap-3">
                            <button type="submit" disabled={busyId === request.id} className={primaryBtn}>
                              {t("approvalList.rejectConfirm")}
                            </button>
                            <button type="button" onClick={handleCancelReject} className={secondaryBtn}>
                              {t("approvalList.rejectCancel")}
                            </button>
                          </div>
                        </form>
                      </td>
                    </tr>
                  )}
                  {expandedId === request.id && (
                    <tr className="bg-hover-bg" data-stack="detail">
                      <td colSpan={5} className="px-2 py-2">
                        <pre className="m-0 whitespace-pre-wrap break-words">
                          {JSON.stringify(request.payload, null, 2)}
                        </pre>
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
        </div>
      )}
    </section>
  );
}
