"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  approveApprovalRequest,
  getDocument,
  getFolder,
  listApprovalRequests,
  rejectApprovalRequest,
  type ApprovalRequest,
} from "@/lib/api";
import { principalName, principalTitle, usePrincipalNames } from "@/lib/usePrincipalNames";

function objectId(request: ApprovalRequest): string {
  return String(request.payload.document_id ?? request.payload.folder_id ?? "?");
}

// Minimal approval inbox for the delete-request workflow (5.2, since
// P7-S1c) - deliberately filtered to only `document.delete`/`folder.delete`,
// not a generic all-action-types inbox (that would be the later-planned
// "administrative trash", see PROGRESS.md). Lives in the user UI instead
// of the admin UI, since here regular users approve each other's requests,
// not an administrative process.
// Since P86-S2 `initiated_by` is shown as a name (it is the initiator's
// principal since P85, ADR 0240) and compared with `currentPrincipalId`
// (the user's `sub`) for "cannot approve one's own request". Rejecting asks
// for an optional reason, which the initiator sees on the request.
export function ApprovalsPane({
  token,
  currentPrincipalId,
}: {
  token: string;
  currentPrincipalId: string;
}) {
  const { t } = useI18n();
  const [requests, setRequests] = useState<ApprovalRequest[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  // P83-S7 (punch list item 6): the object's name instead of its raw UUID as
  // the row title. Resolved once per object; null = could not be resolved
  // (e.g. no read permission), which falls back to a short monospaced id.
  const [names, setNames] = useState<Record<string, string | null>>({});
  // The row whose reject form is open, and its reason draft.
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const initiatorNames = usePrincipalNames(
    token,
    requests.map((r) => r.initiated_by)
  );

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

  useEffect(() => {
    const missing = requests.filter((r) => !(objectId(r) in names));
    if (!token || missing.length === 0) return;
    let cancelled = false;
    Promise.allSettled(
      missing.map((r) =>
        r.action_type === "document.delete"
          ? getDocument(token, objectId(r)).then((d) => d.title)
          : getFolder(token, objectId(r)).then((f) => f.name)
      )
    ).then((results) => {
      if (cancelled) return;
      setNames((prev) => {
        const next = { ...prev };
        results.forEach((result, i) => {
          next[objectId(missing[i])] = result.status === "fulfilled" ? result.value : null;
        });
        return next;
      });
    });
    return () => {
      cancelled = true;
    };
  }, [requests, names, token]);

  async function handleApprove(request: ApprovalRequest) {
    setBusyId(request.id);
    setError(null);
    try {
      await approveApprovalRequest(token, request.id);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("approvals.decisionError"));
    } finally {
      setBusyId(null);
    }
  }

  function openReject(request: ApprovalRequest) {
    setRejectingId(request.id);
    setRejectReason("");
  }

  async function handleReject(request: ApprovalRequest, event: FormEvent) {
    event.preventDefault();
    setBusyId(request.id);
    setError(null);
    try {
      await rejectApprovalRequest(token, request.id, rejectReason.trim() || null);
      setRejectingId(null);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("approvals.decisionError"));
    } finally {
      setBusyId(null);
    }
  }

  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";

  return (
    <section className="approvals-pane" aria-label={t("approvals.paneLabel")}>
      <h2 className="m-0 mb-3 text-base">{t("approvals.heading")}</h2>
      <p className="text-sm opacity-80">{t("approvals.hint")}</p>

      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : requests.length === 0 ? (
        <p className="italic opacity-70">{t("approvals.empty")}</p>
      ) : (
        <ul className="entry-list">
          {requests.map((request) => {
            const isOwnRequest = !!currentPrincipalId && request.initiated_by === currentPrincipalId;
            const isRejecting = rejectingId === request.id;
            return (
              <li className="entry-row" key={request.id}>
                <span className="entry-name">
                  {request.action_type === "document.delete"
                    ? t("approvals.typeDocument")
                    : t("approvals.typeFolder")}{" "}
                  {names[objectId(request)] ? (
                    <span>{names[objectId(request)]}</span>
                  ) : (
                    <code className="font-mono text-xs opacity-80" title={objectId(request)}>
                      {objectId(request).slice(0, 8)}
                    </code>
                  )}{" "}
                  —{" "}
                  <span title={principalTitle(initiatorNames, request.initiated_by)}>
                    {t("approvals.requestedBy", {
                      name: principalName(initiatorNames, request.initiated_by),
                    })}
                  </span>
                </span>
                {/* P83-S6 (item 5): row actions are secondary - a filled primary on
                    every row left the eye nowhere to land */}
                <span className="approval-actions flex gap-3">
                  <button
                    type="button"
                    className={secondaryBtn}
                    onClick={() => handleApprove(request)}
                    disabled={busyId === request.id || isOwnRequest}
                    title={isOwnRequest ? t("approvals.cannotApproveOwn") : undefined}
                  >
                    {t("approvals.approve")}
                  </button>
                  <button
                    type="button"
                    className={secondaryBtn}
                    onClick={() => openReject(request)}
                    disabled={busyId === request.id || isRejecting}
                    aria-expanded={isRejecting}
                  >
                    {t("approvals.reject")}
                  </button>
                </span>
                {isRejecting && (
                  <form
                    className="approval-reject flex flex-wrap items-end gap-3"
                    onSubmit={(event) => handleReject(request, event)}
                  >
                    <label className="flex min-w-0 flex-1 flex-col gap-1 text-sm">
                      {t("approvals.rejectReasonLabel")}
                      <input
                        value={rejectReason}
                        onChange={(event) => setRejectReason(event.target.value)}
                        placeholder={t("approvals.rejectReasonPlaceholder")}
                        autoFocus
                        className="min-h-10 rounded-md border border-border bg-bg px-2 text-fg outline-none focus:border-accent focus:ring-2 focus:ring-accent-bg"
                      />
                    </label>
                    <span className="flex gap-3">
                      <button type="submit" className={secondaryBtn} disabled={busyId === request.id}>
                        {t("approvals.rejectConfirm")}
                      </button>
                      <button
                        type="button"
                        className={secondaryBtn}
                        onClick={() => setRejectingId(null)}
                      >
                        {t("common.cancel")}
                      </button>
                    </span>
                  </form>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
