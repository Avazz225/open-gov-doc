"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  completeReminder,
  createCaseReminder,
  downloadDocumentVersion,
  getCase,
  getCaseDocument,
  listCaseDocuments,
  listCaseReminders,
  listCases,
  type Case,
  type CaseDocumentReference,
  type Reminder,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Case-browsing UI (14.2, Post-Roadmap Phase 74 Session 1, ADR 0141's own
// named gap - see api.ts's own comment above the case functions for the
// full rationale on why this is a smaller cut than user-ui's CasesPane).
// Same list->detail shape as user-ui's own pane, deliberately duplicated
// rather than shared (ADR 0006).
function formatDate(value: string | null, locale: string): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(locale);
}

function triggerBrowserDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function CasesPane() {
  const { t, locale } = useI18n();
  const { accessToken } = useAuth();
  const token = accessToken ?? "";

  const [cases, setCases] = useState<Case[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!token) return;
    setIsLoading(true);
    setError(null);
    try {
      setCases(await listCases(token, statusFilter || undefined));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("cases.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [token, statusFilter, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  if (selectedCaseId) {
    return (
      <CaseDetail token={token} caseId={selectedCaseId} onBack={() => setSelectedCaseId(null)} />
    );
  }

  return (
    <section aria-label={t("cases.paneLabel")}>
      <h2 className="m-0 mb-3 text-base">{t("cases.heading")}</h2>
      <p className="text-sm opacity-80">{t("cases.hint")}</p>

      <label className="flex flex-col gap-1 text-sm font-medium text-fg">
        {t("cases.statusFilterLabel")}
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded-md border border-border bg-bg px-1 py-0.5 text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg"
        >
          <option value="">{t("cases.statusAll")}</option>
          <option value="open">{t("cases.statusOpen")}</option>
          <option value="closed">{t("cases.statusClosed")}</option>
        </select>
      </label>

      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : cases.length === 0 ? (
        <p className="italic opacity-70">{t("cases.empty")}</p>
      ) : (
        <ul className="mt-2 mb-0 list-none p-0">
          {cases.map((c) => (
            <li
              className="flex items-center justify-between gap-2 border-b border-border py-2"
              key={c.id}
            >
              <button
                type="button"
                className="cursor-pointer border-0 bg-transparent p-0 text-left text-fg"
                onClick={() => setSelectedCaseId(c.id)}
              >
                {c.name}
                {c.vorgangsnummer ? ` (${c.vorgangsnummer})` : ""}
              </button>
              <span className="text-xs opacity-70">
                {c.status === "open" ? t("cases.statusOpen") : t("cases.statusClosed")}
                {" · "}
                {t("cases.createdAt", { date: formatDate(c.created_at, locale) })}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function CaseDetail({
  token,
  caseId,
  onBack,
}: {
  token: string;
  caseId: string;
  onBack: () => void;
}) {
  const { t, locale } = useI18n();

  const [activeCase, setActiveCase] = useState<Case | null>(null);
  const [documents, setDocuments] = useState<CaseDocumentReference[]>([]);
  // `null` = resolution failed, `undefined` = not resolved yet - same idiom
  // as user-ui's CasesPane.tsx.
  const [documentTitles, setDocumentTitles] = useState<Record<string, string | null>>({});
  const [error, setError] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      const [loadedCase, loadedDocuments] = await Promise.all([
        getCase(token, caseId),
        listCaseDocuments(token, caseId),
      ]);
      setActiveCase(loadedCase);
      setDocuments(loadedDocuments);
      const titles: Record<string, string | null> = {};
      await Promise.all(
        loadedDocuments
          .filter((ref) => !ref.document_deleted_at)
          .map(async (ref) => {
            try {
              const doc = await getCaseDocument(token, ref.document_id);
              titles[ref.document_id] = doc.title;
            } catch {
              titles[ref.document_id] = null;
            }
          })
      );
      setDocumentTitles(titles);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("cases.loadError"));
    }
  }, [token, caseId, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleDownload(documentId: string) {
    setDownloadError(null);
    try {
      const doc = await getCaseDocument(token, documentId);
      const blob = await downloadDocumentVersion(token, documentId, doc.current_version_number);
      triggerBrowserDownload(blob, doc.title);
    } catch {
      setDownloadError(t("cases.documentDownloadError"));
    }
  }

  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1 text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";

  if (!activeCase) {
    return (
      <section aria-label={t("cases.paneLabel")}>
        <button type="button" onClick={onBack} className={secondaryBtn}>
          {t("cases.backToList")}
        </button>
        {error ? (
          <p className="text-danger" role="alert">
            {error}
          </p>
        ) : (
          <p>{t("common.loading")}</p>
        )}
      </section>
    );
  }

  return (
    <section aria-label={t("cases.paneLabel")}>
      <button type="button" onClick={onBack} className={secondaryBtn}>
        {t("cases.backToList")}
      </button>
      <h2 className="m-0 mb-3 text-base">{activeCase.name}</h2>
      <p className="text-sm opacity-80">
        {activeCase.vorgangsnummer ? `${activeCase.vorgangsnummer} · ` : ""}
        {activeCase.status === "open" ? t("cases.statusOpen") : t("cases.statusClosed")}
        {" · "}
        {t("cases.createdAt", { date: formatDate(activeCase.created_at, locale) })}
        {activeCase.closed_at
          ? ` · ${t("cases.closedAt", { date: formatDate(activeCase.closed_at, locale) })}`
          : ""}
      </p>

      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      <h3>{t("cases.documentsHeading")}</h3>
      {documents.length === 0 ? (
        <p className="italic opacity-70">{t("cases.documentsEmpty")}</p>
      ) : (
        <ul className="mt-2 mb-0 list-none p-0">
          {documents.map((ref) => (
            <li
              className="flex items-center justify-between gap-2 border-b border-border py-2"
              key={ref.document_id}
            >
              {ref.document_deleted_at ? (
                <span>{t("cases.documentDeleted")}</span>
              ) : documentTitles[ref.document_id] === null ? (
                <span>{t("cases.documentTitleUnavailable")}</span>
              ) : (
                <button
                  type="button"
                  className="cursor-pointer border-0 bg-transparent p-0 text-left text-fg"
                  onClick={() => handleDownload(ref.document_id)}
                >
                  {documentTitles[ref.document_id] ?? ref.document_id}
                </button>
              )}
              <span className="text-xs opacity-70">
                {t("cases.documentAddedAt", { date: formatDate(ref.added_at, locale) })}
              </span>
            </li>
          ))}
        </ul>
      )}
      {downloadError && (
        <p className="text-danger" role="alert">
          {downloadError}
        </p>
      )}

      <CaseReminders token={token} caseId={caseId} />
    </section>
  );
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

// A week from now at 09:00 local - the same default as user-ui's dialog.
function defaultReminderDate(): { date: string; time: string } {
  const date = new Date();
  date.setDate(date.getDate() + 7);
  return {
    date: `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    time: "09:00",
  };
}

// Follow-up reminders on the case (Wiedervorlagen, P90-S2, ADR 0246): a
// reviewer puts the case back in front of themselves, e.g. after asking for
// a missing document. Only the reviewer's own open reminders on this case
// are listed; the full list lives in user-ui's "Wiedervorlagen" view.
function CaseReminders({ token, caseId }: { token: string; caseId: string }) {
  const { t, locale } = useI18n();
  const { user } = useAuth();
  const username = user?.username ?? "";
  const initial = defaultReminderDate();
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [dateInput, setDateInput] = useState(initial.date);
  const [timeInput, setTimeInput] = useState(initial.time);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!token || !username) return;
    try {
      setReminders(await listCaseReminders(token, username, caseId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("cases.remindersLoadError"));
    }
  }, [token, username, caseId, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("cases.remindersActionError"));
    } finally {
      setBusy(false);
    }
  }

  function handleCreate() {
    if (!dateInput) return;
    const dueAt = new Date(`${dateInput}T${timeInput || "09:00"}`).toISOString();
    run(async () => {
      await createCaseReminder(token, {
        user_id: username,
        case_id: caseId,
        due_at: dueAt,
        note: note.trim(),
      });
      setNote("");
    });
  }

  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1 text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1 text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
  const fieldInput =
    "box-border w-full rounded-md border border-border bg-bg px-2 py-1.5 text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";

  return (
    <section aria-labelledby="case-reminders-heading" className="mt-4">
      <h3 id="case-reminders-heading">{t("cases.remindersHeading")}</h3>
      {reminders.length === 0 ? (
        <p className="italic opacity-70">{t("cases.remindersEmpty")}</p>
      ) : (
        <ul className="mt-2 mb-0 list-none p-0">
          {reminders.map((reminder) => (
            <li
              key={reminder.id}
              className="flex flex-wrap items-center justify-between gap-2 border-b border-border py-2"
            >
              <span className="min-w-0">
                {formatDate(reminder.due_at, locale)}
                {reminder.is_due && <strong> · {t("cases.reminderDue")}</strong>}
                {reminder.note && <span className="block text-sm opacity-80">{reminder.note}</span>}
              </span>
              <button
                type="button"
                className={secondaryBtn}
                disabled={busy}
                onClick={() => run(() => completeReminder(token, reminder.id))}
              >
                {t("cases.reminderDone")}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          {t("cases.reminderDate")}
          <input
            type="date"
            className={fieldInput}
            value={dateInput}
            onChange={(e) => setDateInput(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          {t("cases.reminderTime")}
          <input
            type="time"
            className={fieldInput}
            value={timeInput}
            onChange={(e) => setTimeInput(e.target.value)}
          />
        </label>
        <label className="flex min-w-[12rem] flex-1 flex-col gap-1 text-sm">
          {t("cases.reminderNote")}
          <input
            type="text"
            className={fieldInput}
            maxLength={2000}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
        <button type="button" className={primaryBtn} disabled={busy || !dateInput} onClick={handleCreate}>
          {t("cases.reminderSet")}
        </button>
      </div>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
