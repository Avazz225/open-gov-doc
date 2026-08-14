"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  assignInboundMessage,
  confirmInboundMatch,
  listInboundMessages,
  listOutboundMessages,
  rejectInboundMessage,
  sendOutboundMessage,
  type InboundMessage,
  type OutboundMessage,
} from "@/lib/api";

// Inbox/outbox (2.5/3.3, P15-S3) - external correspondence not yet
// assigned to a case. Unlike the trash, the concept specifies NO
// "personal" view here - the icon rail entry itself is already role-gated
// in IconRail.tsx (`dms-poststelle`), this component itself does not check
// any role.
type Tab = "inbox" | "outbox";

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

  const reload = useCallback(async () => {
    if (!token) return;
    setIsLoading(true);
    setError(null);
    try {
      if (tab === "inbox") {
        setInbound(await listInboundMessages(token));
      } else {
        setOutbound(await listOutboundMessages(token));
      }
    } catch {
      setError(t("poststelle.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [token, tab, t]);

  useEffect(() => {
    reload();
  }, [reload]);

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
      </span>

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
                    <button
                      type="button"
                      onClick={() => reject(message)}
                      disabled={busyId === message.id}
                    >
                      {t("poststelle.reject")}
                    </button>
                  </span>
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
              </li>
            ))}
          </ul>
        )
      ) : (
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
      )}
    </section>
  );
}
