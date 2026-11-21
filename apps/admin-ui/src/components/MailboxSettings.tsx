"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  createMailboxConfig,
  deleteMailboxConfig,
  listGroups,
  listMailboxConfig,
  testMailboxConfig,
  updateMailboxConfig,
  type Group,
  type MailboxConfig,
  type MailboxConfigInput,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// mail-connector mailboxes (P88-S4b, ADR 0244). The table and API came in
// P88-S4a; before, mailboxes were a JSON env variable changed by restart.
// - The password is write-only: the field stays empty when editing, and
//   leaving it empty keeps the stored one ("unverändert").
// - "Verbindung testen" logs in with the form as it is (falling back to the
//   stored password), reading no message.
// - A mailbox with messages can only be disabled; delete is offered only
//   without messages.
// - The page needs `admin.mail_config`; the route guard and the server
//   both check it.

type Draft = {
  id: string;
  name: string;
  kind: "central" | "departmental";
  owningGroupId: string;
  protocol: "pop3" | "imap";
  host: string;
  port: string;
  username: string;
  password: string;
  useTls: boolean;
  imapMailbox: string;
  enabled: boolean;
};

const EMPTY_DRAFT: Draft = {
  id: "",
  name: "",
  kind: "central",
  owningGroupId: "",
  protocol: "imap",
  host: "",
  port: "",
  username: "",
  password: "",
  useTls: true,
  imapMailbox: "INBOX",
  enabled: true,
};

export function defaultPort(protocol: "pop3" | "imap", useTls: boolean): number {
  if (protocol === "pop3") return useTls ? 995 : 110;
  return useTls ? 993 : 143;
}

function draftFrom(mailbox: MailboxConfig): Draft {
  return {
    id: mailbox.id,
    name: mailbox.name,
    kind: mailbox.kind,
    owningGroupId: mailbox.owning_group_id ?? "",
    protocol: mailbox.inbound_protocol,
    host: mailbox.host,
    port: String(mailbox.port),
    username: mailbox.username,
    password: "",
    useTls: mailbox.use_tls,
    imapMailbox: mailbox.imap_mailbox,
    enabled: mailbox.enabled,
  };
}

export function toInput(draft: Draft): MailboxConfigInput {
  return {
    id: draft.id.trim(),
    name: draft.name.trim(),
    kind: draft.kind,
    owning_group_id: draft.kind === "departmental" ? draft.owningGroupId.trim() || null : null,
    inbound_protocol: draft.protocol,
    host: draft.host.trim(),
    port: draft.port.trim() ? Number(draft.port) : null,
    username: draft.username.trim(),
    // Empty = keep the stored password (write-only on the server).
    password: draft.password ? draft.password : null,
    use_tls: draft.useTls,
    imap_mailbox: draft.imapMailbox.trim() || "INBOX",
    enabled: draft.enabled,
  };
}

const primaryBtn =
  "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
const secondaryBtn =
  "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
const fieldInput =
  "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none focus:border-accent focus:ring-2 focus:ring-accent-bg";

export function MailboxSettings() {
  const { accessToken } = useAuth();
  const { t, locale } = useI18n();
  const [mailboxes, setMailboxes] = useState<MailboxConfig[]>([]);
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // null = no form open; editingId null with a form open = creating.
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; error: string | null } | null>(null);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    try {
      setMailboxes(await listMailboxConfig(accessToken));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [accessToken, t]);

  useEffect(() => {
    reload();
    if (!accessToken) return;
    // Departmental owners are permission-service groups; without the list
    // (unreachable), the group id is typed in.
    listGroups(accessToken)
      .then(setGroups)
      .catch(() => setGroups(null));
  }, [reload, accessToken]);

  const editing = editingId ? mailboxes.find((m) => m.id === editingId) : undefined;
  const groupName = (id: string | null) => groups?.find((g) => g.id === id)?.name ?? id ?? "—";

  function open(mailbox?: MailboxConfig) {
    setError(null);
    setTestResult(null);
    setEditingId(mailbox?.id ?? null);
    setDraft(mailbox ? draftFrom(mailbox) : { ...EMPTY_DRAFT });
  }

  function patch(next: Partial<Draft>) {
    setDraft((prev) => (prev ? { ...prev, ...next } : prev));
    setTestResult(null);
  }

  async function run<T>(action: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    setError(null);
    try {
      return await action();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.actionError"));
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft || !accessToken) return;
    const input = toInput(draft);
    const saved = await run(() =>
      editingId ? updateMailboxConfig(accessToken, input) : createMailboxConfig(accessToken, input)
    );
    if (saved) {
      setDraft(null);
      setEditingId(null);
      await reload();
    }
  }

  async function test() {
    if (!draft || !accessToken) return;
    const result = await run(() => testMailboxConfig(accessToken, toInput(draft)));
    if (result) setTestResult(result);
  }

  async function setEnabled(mailbox: MailboxConfig, enabled: boolean) {
    if (!accessToken) return;
    const done = await run(() => updateMailboxConfig(accessToken, { ...toInput(draftFrom(mailbox)), enabled }));
    if (done) await reload();
  }

  async function remove(mailbox: MailboxConfig) {
    if (!accessToken || !window.confirm(t("mailboxes.deleteConfirm", { name: mailbox.name }))) return;
    const done = await run(async () => {
      await deleteMailboxConfig(accessToken, mailbox.id);
      return true;
    });
    if (done) await reload();
  }

  const formatDate = (value: string | null) => (value ? new Date(value).toLocaleString(locale) : "—");

  return (
    <>
      <p className="text-sm opacity-80">{t("mailboxes.hint")}</p>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
      {!draft && (
        <button type="button" className={`${primaryBtn} mb-4`} onClick={() => open()}>
          {t("mailboxes.add")}
        </button>
      )}

      {draft && (
        <section className="rounded-lg border border-border p-4 mb-6">
          <h2>{editingId ? t("mailboxes.editHeading", { name: editing?.name ?? editingId }) : t("mailboxes.newHeading")}</h2>
          <form
            aria-label={editingId ? t("mailboxes.editFormLabel") : t("mailboxes.newFormLabel")}
            className="flex flex-col gap-3"
            onSubmit={save}
          >
            <div className="flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1 text-sm">
                {t("mailboxes.id")}
                <input
                  value={draft.id}
                  onChange={(e) => patch({ id: e.target.value })}
                  required
                  disabled={editingId !== null}
                  pattern="[a-z0-9][a-z0-9_\-]{0,63}"
                  title={t("mailboxes.idRule")}
                  className={`${fieldInput} w-40 font-mono`}
                />
              </label>
              <label className="flex flex-col gap-1 text-sm">
                {t("mailboxes.name")}
                <input value={draft.name} onChange={(e) => patch({ name: e.target.value })} required className={fieldInput} />
              </label>
              <label className="flex flex-col gap-1 text-sm">
                {t("mailboxes.kind")}
                <select
                  value={draft.kind}
                  onChange={(e) => patch({ kind: e.target.value as Draft["kind"] })}
                  className={fieldInput}
                >
                  <option value="central">{t("mailboxes.kinds.central")}</option>
                  <option value="departmental">{t("mailboxes.kinds.departmental")}</option>
                </select>
              </label>
              {draft.kind === "departmental" && (
                <label className="flex flex-col gap-1 text-sm">
                  {t("mailboxes.owningGroup")}
                  {groups ? (
                    <select
                      value={draft.owningGroupId}
                      onChange={(e) => patch({ owningGroupId: e.target.value })}
                      required
                      className={fieldInput}
                    >
                      <option value="">{t("mailboxes.chooseGroup")}</option>
                      {groups.map((g) => (
                        <option key={g.id} value={g.id}>
                          {g.name}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      value={draft.owningGroupId}
                      onChange={(e) => patch({ owningGroupId: e.target.value })}
                      required
                      className={fieldInput}
                    />
                  )}
                </label>
              )}
            </div>

            <fieldset className="rounded-md border border-border p-3">
              <legend className="px-1 text-sm font-semibold">{t("mailboxes.connection")}</legend>
              <div className="flex flex-wrap items-end gap-3">
                <label className="flex flex-col gap-1 text-sm">
                  {t("mailboxes.protocol")}
                  <select
                    value={draft.protocol}
                    onChange={(e) => patch({ protocol: e.target.value as Draft["protocol"] })}
                    className={fieldInput}
                  >
                    <option value="imap">IMAP</option>
                    <option value="pop3">POP3</option>
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  {t("mailboxes.host")}
                  <input value={draft.host} onChange={(e) => patch({ host: e.target.value })} required className={fieldInput} />
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  {t("mailboxes.port")}
                  <input
                    type="number"
                    min={1}
                    max={65535}
                    value={draft.port}
                    placeholder={String(defaultPort(draft.protocol, draft.useTls))}
                    onChange={(e) => patch({ port: e.target.value })}
                    className={`${fieldInput} w-24`}
                  />
                </label>
                <label className="flex items-center gap-1 pb-2 text-sm">
                  <input type="checkbox" checked={draft.useTls} onChange={(e) => patch({ useTls: e.target.checked })} />
                  {t("mailboxes.useTls")}
                </label>
                {draft.protocol === "imap" && (
                  <label className="flex flex-col gap-1 text-sm">
                    {t("mailboxes.imapMailbox")}
                    <input
                      value={draft.imapMailbox}
                      onChange={(e) => patch({ imapMailbox: e.target.value })}
                      className={`${fieldInput} w-32`}
                    />
                  </label>
                )}
              </div>
              <div className="mt-3 flex flex-wrap items-end gap-3">
                <label className="flex flex-col gap-1 text-sm">
                  {t("mailboxes.username")}
                  <input
                    value={draft.username}
                    onChange={(e) => patch({ username: e.target.value })}
                    required
                    autoComplete="off"
                    className={fieldInput}
                  />
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  {t("mailboxes.password")}
                  <input
                    type="password"
                    value={draft.password}
                    onChange={(e) => patch({ password: e.target.value })}
                    required={!editing?.has_password}
                    autoComplete="new-password"
                    placeholder={editing?.has_password ? t("mailboxes.passwordUnchanged") : ""}
                    className={fieldInput}
                  />
                </label>
              </div>
              <p className="mb-0 mt-2 text-xs opacity-70">{t("mailboxes.passwordHint")}</p>
            </fieldset>

            <label className="flex items-center gap-1 text-sm">
              <input type="checkbox" checked={draft.enabled} onChange={(e) => patch({ enabled: e.target.checked })} />
              {t("mailboxes.enabledLabel")}
            </label>

            <div className="flex flex-wrap items-center gap-3">
              <button type="submit" className={primaryBtn} disabled={busy}>
                {t("common.save")}
              </button>
              <button type="button" className={secondaryBtn} disabled={busy} onClick={test}>
                {t("mailboxes.test")}
              </button>
              <button
                type="button"
                className={secondaryBtn}
                onClick={() => {
                  setDraft(null);
                  setEditingId(null);
                }}
              >
                {t("common.cancel")}
              </button>
              {testResult && (
                <span role="status" className={`badge ${testResult.ok ? "ok" : "down"}`}>
                  {testResult.ok ? t("mailboxes.testOk") : t("mailboxes.testFailed")}
                </span>
              )}
            </div>
            {testResult && !testResult.ok && testResult.error && (
              <p className="m-0 font-mono text-xs text-danger">{testResult.error}</p>
            )}
          </form>
        </section>
      )}

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : mailboxes.length === 0 ? (
        <p className="italic opacity-70">{t("mailboxes.empty")}</p>
      ) : (
        <div className="dms-scroll-x">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("mailboxes.name")}</th>
                <th>{t("mailboxes.kind")}</th>
                <th>{t("mailboxes.connection")}</th>
                <th>{t("mailboxes.lastPoll")}</th>
                <th>{t("mailboxes.messages")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {mailboxes.map((mailbox) => (
                <tr key={mailbox.id}>
                  <td>
                    <div className="whitespace-nowrap">{mailbox.name}</div>
                    <code className="font-mono text-xs opacity-70">{mailbox.id}</code>
                    {!mailbox.enabled && <span className="badge neutral ml-2">{t("mailboxes.disabled")}</span>}
                  </td>
                  <td className="text-sm">
                    {t(`mailboxes.kinds.${mailbox.kind}`)}
                    {mailbox.kind === "departmental" && (
                      <div className="opacity-70">{groupName(mailbox.owning_group_id)}</div>
                    )}
                  </td>
                  <td className="text-sm">
                    <div className="whitespace-nowrap">
                      {mailbox.inbound_protocol.toUpperCase()} {mailbox.host}:{mailbox.port}
                      {mailbox.use_tls ? " · TLS" : ""}
                    </div>
                    <div className="opacity-70">
                      {mailbox.username}
                      {!mailbox.has_password && (
                        <span className="ml-1 text-danger">{t("mailboxes.noPassword")}</span>
                      )}
                    </div>
                  </td>
                  <td className="text-sm">
                    {mailbox.last_poll_at === null ? (
                      <span className="opacity-70">{mailbox.enabled ? t("mailboxes.notPolledYet") : "—"}</span>
                    ) : (
                      <>
                        <span className={`badge ${mailbox.last_poll_ok ? "ok" : "down"}`}>
                          {mailbox.last_poll_ok ? t("mailboxes.pollOk") : t("mailboxes.pollFailed")}
                        </span>
                        <div className="whitespace-nowrap opacity-70">{formatDate(mailbox.last_poll_at)}</div>
                        {mailbox.last_poll_error && (
                          <div className="max-w-xs break-words font-mono text-xs text-danger">{mailbox.last_poll_error}</div>
                        )}
                      </>
                    )}
                  </td>
                  <td>{mailbox.message_count}</td>
                  <td>
                    <div className="flex flex-wrap gap-2">
                      <button type="button" className={secondaryBtn} disabled={busy} onClick={() => open(mailbox)}>
                        {t("mailboxes.edit")}
                      </button>
                      <button
                        type="button"
                        className={secondaryBtn}
                        disabled={busy}
                        onClick={() => setEnabled(mailbox, !mailbox.enabled)}
                      >
                        {mailbox.enabled ? t("mailboxes.disable") : t("mailboxes.enable")}
                      </button>
                      {mailbox.message_count === 0 && (
                        <button type="button" className={secondaryBtn} disabled={busy} onClick={() => remove(mailbox)}>
                          {t("common.delete")}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
