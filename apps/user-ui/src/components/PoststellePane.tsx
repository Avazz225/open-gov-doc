"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  assignInboundMessage,
  confirmInboundMatch,
  listInboundMessages,
  listMailboxes,
  listOutboundMessages,
  rejectInboundMessage,
  routeInboundMessage,
  searchRoutingLog,
  sendOutboundMessage,
  type InboundMessage,
  type MailboxInfo,
  type OutboundMessage,
  type RoutingLogEntryWithMessage,
} from "@/lib/api";

// Inbox/outbox (2.5/3.3, P15-S3) - external correspondence not yet
// assigned to a case. Unlike the trash, the concept specifies NO
// "personal" view here - the icon rail entry itself is already role-gated
// in IconRail.tsx (`dms-poststelle`), this component itself does not check
// any role. "postbuch" (searchable cross-message routing register, 14.2,
// post-roadmap phase 31 session 12c) only ever renders once more than one
// mailbox is configured - with a single mailbox, routing (and thus a
// routing register) can never have happened, same gate as the mailbox
// filter/routing action from session 12b.
type Tab = "inbox" | "outbox" | "postbuch";

function statusLabel(t: ReturnType<typeof useI18n>["t"], status: InboundMessage["status"]): string {
  return t(`poststelle.status.${status}`);
}

export function PoststellePane({ token }: { token: string }) {
  const { t } = useI18n();
  const [tab, setTab] = useState<Tab>("inbox");
  const [inbound, setInbound] = useState<InboundMessage[]>([]);
  const [outbound, setOutbound] = useState<OutboundMessage[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionId, setActionId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [folderId, setFolderId] = useState("");
  const [caseId, setCaseId] = useState("");
  const [composeOpen, setComposeOpen] = useState(false);
  const [composeTo, setComposeTo] = useState("");
  const [composeSubject, setComposeSubject] = useState("");
  const [composeBody, setComposeBody] = useState("");
  // Multi-inbox model & routing between mailboxes (14.2, post-roadmap phase
  // 31 sessions 12a/12b).
  const [mailboxes, setMailboxes] = useState<MailboxInfo[]>([]);
  const [mailboxFilter, setMailboxFilter] = useState("");
  const [routingId, setRoutingId] = useState<string | null>(null);
  const [routeTargetMailboxId, setRouteTargetMailboxId] = useState("");
  const [routeReason, setRouteReason] = useState("");
  // Searchable "Postbuch" register (14.2, post-roadmap phase 31 session
  // 12c).
  const [postbuch, setPostbuch] = useState<RoutingLogEntryWithMessage[]>([]);
  const [postbuchMailboxFilter, setPostbuchMailboxFilter] = useState("");
  const [postbuchQuery, setPostbuchQuery] = useState("");

  useEffect(() => {
    if (!token) return;
    listMailboxes(token)
      .then(setMailboxes)
      .catch(() => setMailboxes([]));
  }, [token]);

  function mailboxName(mailboxId: string): string {
    return mailboxes.find((m) => m.id === mailboxId)?.name ?? mailboxId;
  }

  const reload = useCallback(
    async (postbuchQueryOverride?: string) => {
      if (!token) return;
      setIsLoading(true);
      setError(null);
      try {
        if (tab === "inbox") {
          setInbound(await listInboundMessages(token, { mailboxId: mailboxFilter || undefined }));
        } else if (tab === "outbox") {
          setOutbound(await listOutboundMessages(token));
        } else {
          setPostbuch(
            await searchRoutingLog(token, {
              mailboxId: postbuchMailboxFilter || undefined,
              q: (postbuchQueryOverride ?? postbuchQuery).trim() || undefined,
            })
          );
        }
      } catch {
        setError(tab === "postbuch" ? t("poststelle.postbuchLoadError") : t("poststelle.loadError"));
      } finally {
        setIsLoading(false);
      }
    },
    // `postbuchQuery` deliberately excluded - it's a free-text field, only
    // applied on explicit form submit (`runPostbuchSearch`), not on every
    // keystroke (same pattern as `AussonderungPane`'s `query`/`runSearch`).
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [token, tab, mailboxFilter, postbuchMailboxFilter, t]
  );

  useEffect(() => {
    reload();
  }, [reload]);

  function runPostbuchSearch(e: React.FormEvent) {
    e.preventDefault();
    reload(postbuchQuery);
  }

  function startAction(message: InboundMessage) {
    setActionId(message.id);
    setTitle(message.subject);
    setFolderId("");
    setCaseId("");
    setError(null);
  }

  async function confirmMatch(message: InboundMessage) {
    setBusyId(message.id);
    setError(null);
    try {
      await confirmInboundMatch(token, message.id, { title, folderId: folderId.trim() || undefined });
      setActionId(null);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("poststelle.confirmError"));
    } finally {
      setBusyId(null);
    }
  }

  async function assignManually(message: InboundMessage) {
    if (!folderId.trim()) return;
    setBusyId(message.id);
    setError(null);
    try {
      await assignInboundMessage(token, message.id, {
        title,
        folderId: folderId.trim(),
        caseId: caseId.trim() || undefined,
      });
      setActionId(null);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("poststelle.assignError"));
    } finally {
      setBusyId(null);
    }
  }

  async function reject(message: InboundMessage) {
    if (!window.confirm(t("poststelle.rejectConfirm", { subject: message.subject }))) return;
    setBusyId(message.id);
    setError(null);
    try {
      await rejectInboundMessage(token, message.id);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("poststelle.rejectError"));
    } finally {
      setBusyId(null);
    }
  }

  function startRouting(message: InboundMessage) {
    setRoutingId(message.id);
    setRouteTargetMailboxId("");
    setRouteReason("");
    setError(null);
  }

  async function routeMessage(message: InboundMessage) {
    if (!routeTargetMailboxId) return;
    setBusyId(message.id);
    setError(null);
    try {
      await routeInboundMessage(token, message.id, {
        targetMailboxId: routeTargetMailboxId,
        reason: routeReason.trim() || undefined,
      });
      setRoutingId(null);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("poststelle.routeError"));
    } finally {
      setBusyId(null);
    }
  }

  async function sendCompose() {
    setError(null);
    try {
      await sendOutboundMessage(token, {
        toAddress: composeTo,
        subject: composeSubject,
        body: composeBody,
      });
      setComposeOpen(false);
      setComposeTo("");
      setComposeSubject("");
      setComposeBody("");
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("poststelle.sendError"));
    }
  }

  return (
    <section className="poststelle-pane" aria-label={t("poststelle.paneLabel")}>
      <h2 className="pane-heading">{t("poststelle.heading")}</h2>
      <p className="hint">{t("poststelle.hint")}</p>

      <span className="view-mode-toggle" role="group" aria-label={t("poststelle.tabLabel")}>
        <button
          type="button"
          className={tab === "inbox" ? "view-mode-active" : undefined}
          aria-pressed={tab === "inbox"}
          onClick={() => setTab("inbox")}
        >
          {t("poststelle.inbox")}
        </button>
        <button
          type="button"
          className={tab === "outbox" ? "view-mode-active" : undefined}
          aria-pressed={tab === "outbox"}
          onClick={() => setTab("outbox")}
        >
          {t("poststelle.outbox")}
        </button>
        {mailboxes.length > 1 && (
          <button
            type="button"
            className={tab === "postbuch" ? "view-mode-active" : undefined}
            aria-pressed={tab === "postbuch"}
            onClick={() => setTab("postbuch")}
          >
            {t("poststelle.postbuch")}
          </button>
        )}
      </span>

      {tab === "inbox" && mailboxes.length > 1 && (
        <label className="poststelle-mailbox-filter">
          {t("poststelle.mailboxFilterLabel")}
          <select value={mailboxFilter} onChange={(e) => setMailboxFilter(e.target.value)}>
            <option value="">{t("poststelle.mailboxFilterAll")}</option>
            {mailboxes.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
      )}

      {tab === "postbuch" && (
        <form className="poststelle-postbuch-search" onSubmit={runPostbuchSearch}>
          <label>
            {t("poststelle.postbuchMailboxLabel")}
            <select
              value={postbuchMailboxFilter}
              onChange={(e) => setPostbuchMailboxFilter(e.target.value)}
            >
              <option value="">{t("poststelle.postbuchMailboxAll")}</option>
              {mailboxes.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("poststelle.postbuchSearchLabel")}
            <input
              type="text"
              value={postbuchQuery}
              onChange={(e) => setPostbuchQuery(e.target.value)}
              placeholder={t("poststelle.postbuchSearchPlaceholder")}
            />
          </label>
          <button type="submit" disabled={isLoading}>
            {t("poststelle.postbuchSearchSubmit")}
          </button>
        </form>
      )}

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : tab === "inbox" ? (
        inbound.length === 0 ? (
          <p className="empty-state">{t("poststelle.inboxEmpty")}</p>
        ) : (
          <ul className="entry-list">
            {inbound.map((message) => (
              <li className="entry-row" key={message.id}>
                <span className="entry-name">
                  {message.subject} — {message.from_address} ({statusLabel(t, message.status)})
                  {message.match_value && ` · ${message.match_type}: ${message.match_value}`}
                  {mailboxes.length > 1 && ` · ${mailboxName(message.mailbox_id)}`}
                </span>
                {(message.status === "unassigned" || message.status === "proposed_match") && (
                  <span className="actions">
                    <button
                      type="button"
                      onClick={() => startAction(message)}
                      disabled={busyId === message.id}
                    >
                      {message.status === "proposed_match"
                        ? t("poststelle.confirm")
                        : t("poststelle.assign")}
                    </button>
                    {mailboxes.length > 1 && (
                      <button
                        type="button"
                        onClick={() => startRouting(message)}
                        disabled={busyId === message.id}
                      >
                        {t("poststelle.route")}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => reject(message)}
                      disabled={busyId === message.id}
                    >
                      {t("poststelle.reject")}
                    </button>
                  </span>
                )}
                {routingId === message.id && (
                  <div className="poststelle-action-form">
                    <label>
                      {t("poststelle.routeTargetLabel")}
                      <select
                        value={routeTargetMailboxId}
                        onChange={(e) => setRouteTargetMailboxId(e.target.value)}
                      >
                        <option value="">{t("poststelle.routeTargetPlaceholder")}</option>
                        {mailboxes
                          .filter((m) => m.id !== message.mailbox_id)
                          .map((m) => (
                            <option key={m.id} value={m.id}>
                              {m.name}
                            </option>
                          ))}
                      </select>
                    </label>
                    <label>
                      {t("poststelle.routeReasonLabel")}
                      <input
                        type="text"
                        value={routeReason}
                        onChange={(e) => setRouteReason(e.target.value)}
                      />
                    </label>
                    <span className="actions">
                      <button
                        type="button"
                        disabled={busyId === message.id || !routeTargetMailboxId}
                        onClick={() => routeMessage(message)}
                      >
                        {t("poststelle.routeSubmit")}
                      </button>
                      <button type="button" onClick={() => setRoutingId(null)}>
                        {t("poststelle.actionCancel")}
                      </button>
                    </span>
                  </div>
                )}
                {actionId === message.id && (
                  <div className="poststelle-action-form">
                    <label>
                      {t("poststelle.titleLabel")}
                      <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} />
                    </label>
                    <label>
                      {message.status === "proposed_match"
                        ? t("poststelle.folderOverrideLabel")
                        : t("poststelle.folderLabel")}
                      <input
                        type="text"
                        value={folderId}
                        onChange={(e) => setFolderId(e.target.value)}
                      />
                    </label>
                    {message.status === "unassigned" && (
                      <label>
                        {t("poststelle.caseLabel")}
                        <input
                          type="text"
                          value={caseId}
                          onChange={(e) => setCaseId(e.target.value)}
                        />
                      </label>
                    )}
                    <span className="actions">
                      <button
                        type="button"
                        disabled={
                          busyId === message.id ||
                          title.trim() === "" ||
                          (message.status === "unassigned" && folderId.trim() === "")
                        }
                        onClick={() =>
                          message.status === "proposed_match"
                            ? confirmMatch(message)
                            : assignManually(message)
                        }
                      >
                        {t("poststelle.actionConfirm")}
                      </button>
                      <button type="button" onClick={() => setActionId(null)}>
                        {t("poststelle.actionCancel")}
                      </button>
                    </span>
                  </div>
                )}
                {message.attachments.length > 0 && (
                  <ul className="poststelle-attachments">
                    {message.attachments.map((a) => (
                      <li key={a.id}>
                        {a.filename} ({a.scan_status})
                      </li>
                    ))}
                  </ul>
                )}
                {message.routing_log.length > 0 && (
                  <ul className="poststelle-routing-log">
                    {message.routing_log.map((entry) => (
                      <li key={entry.id}>
                        <span>
                          {t("poststelle.routeLogEntry", {
                            from: mailboxName(entry.from_mailbox_id),
                            to: mailboxName(entry.to_mailbox_id),
                            by: entry.routed_by,
                          })}
                        </span>
                        {entry.reason && ` — ${entry.reason}`}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )
      ) : tab === "outbox" ? (
        <>
          <button type="button" onClick={() => setComposeOpen((prev) => !prev)}>
            {t("poststelle.compose")}
          </button>
          {composeOpen && (
            <div className="poststelle-action-form">
              <label>
                {t("poststelle.toLabel")}
                <input
                  type="text"
                  value={composeTo}
                  onChange={(e) => setComposeTo(e.target.value)}
                />
              </label>
              <label>
                {t("poststelle.subjectLabel")}
                <input
                  type="text"
                  value={composeSubject}
                  onChange={(e) => setComposeSubject(e.target.value)}
                />
              </label>
              <label>
                {t("poststelle.bodyLabel")}
                <textarea value={composeBody} onChange={(e) => setComposeBody(e.target.value)} />
              </label>
              <button
                type="button"
                disabled={!composeTo.trim() || !composeSubject.trim() || !composeBody.trim()}
                onClick={sendCompose}
              >
                {t("poststelle.send")}
              </button>
            </div>
          )}
          {outbound.length === 0 ? (
            <p className="empty-state">{t("poststelle.outboxEmpty")}</p>
          ) : (
            <ul className="entry-list">
              {outbound.map((message) => (
                <li className="entry-row" key={message.id}>
                  <span className="entry-name">
                    {message.subject} → {message.to_address} ({message.status})
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : postbuch.length === 0 ? (
        <p className="empty-state">{t("poststelle.postbuchEmpty")}</p>
      ) : (
        <ul className="entry-list poststelle-postbuch-list">
          {postbuch.map((entry) => (
            <li className="entry-row" key={entry.id}>
              <span className="entry-name">{entry.message_subject}</span>
              <span className="entry-meta">
                {t("poststelle.postbuchEntry", {
                  from: mailboxName(entry.from_mailbox_id),
                  to: mailboxName(entry.to_mailbox_id),
                  by: entry.routed_by,
                })}
                {entry.reason && ` — ${entry.reason}`}
                {" · "}
                {t("poststelle.postbuchCurrentLocation", {
                  mailbox: mailboxName(entry.message_current_mailbox_id),
                })}
                {" · "}
                {statusLabel(t, entry.message_status)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
