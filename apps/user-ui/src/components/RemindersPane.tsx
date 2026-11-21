"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  completeReminder,
  deleteReminder,
  getCase,
  getDocument,
  getFolder,
  listReminders,
  reopenReminder,
  snoozeReminder,
  type Case,
  type DocumentSummary,
  type Folder,
  type Reminder,
} from "@/lib/api";
import {
  announceRemindersChanged,
  formatDueAt,
  presetDate,
  type ReminderPreset,
} from "@/lib/reminders";

interface ResolvedReminder {
  reminder: Reminder;
  document: DocumentSummary | null;
  folder: Folder | null;
  case: Case | null;
}

const SNOOZE_PRESETS: ReminderPreset[] = ["tomorrow", "nextWeek"];
// Done reminders are history; the latest are enough to undo a misclick.
const DONE_SHOWN = 20;

// "Wiedervorlagen" view (P90-S2, ADR 0246). The list is the reminders' real
// channel: no view lists a person's own in-app notifications, so what is due
// is shown here (and counted on the icon rail's badge). Three groups: due,
// upcoming, and recently done. Display names are resolved live like
// FavoritesPane; an object that is gone or no longer readable stays listed
// (with a placeholder) so the reminder can still be completed or deleted.
export function RemindersPane({
  token,
  currentUsername,
  onOpenDocument,
  onOpenFolder,
  onOpenCase,
}: {
  token: string;
  currentUsername: string;
  onOpenDocument: (doc: DocumentSummary) => void;
  onOpenFolder: (folder: Folder) => void;
  onOpenCase: (caseItem: Case) => void;
}) {
  const { t, locale } = useI18n();
  const [open, setOpen] = useState<ResolvedReminder[]>([]);
  const [done, setDone] = useState<ResolvedReminder[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const resolve = useCallback(
    async (reminder: Reminder): Promise<ResolvedReminder> => {
      const empty = { reminder, document: null, folder: null, case: null };
      if (reminder.object_type === "document") {
        return { ...empty, document: await getDocument(token, reminder.object_id).catch(() => null) };
      }
      if (reminder.object_type === "case") {
        return { ...empty, case: await getCase(token, reminder.object_id).catch(() => null) };
      }
      return { ...empty, folder: await getFolder(token, reminder.object_id).catch(() => null) };
    },
    [token]
  );

  const reload = useCallback(async () => {
    if (!token || !currentUsername) return;
    setIsLoading(true);
    setError(null);
    try {
      const [openRows, doneRows] = await Promise.all([
        listReminders(token, currentUsername, { status: "open" }),
        listReminders(token, currentUsername, { status: "done" }),
      ]);
      const recentDone = doneRows
        .sort((a, b) => (b.completed_at ?? "").localeCompare(a.completed_at ?? ""))
        .slice(0, DONE_SHOWN);
      setOpen(await Promise.all(openRows.map(resolve)));
      setDone(await Promise.all(recentDone.map(resolve)));
    } catch {
      setError(t("reminders.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [token, currentUsername, resolve, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function run(reminder: Reminder, action: () => Promise<unknown>) {
    setBusyId(reminder.id);
    setError(null);
    try {
      await action();
      announceRemindersChanged();
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("reminders.actionError"));
    } finally {
      setBusyId(null);
    }
  }

  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";

  function typeLabel(reminder: Reminder): string {
    if (reminder.object_type === "document") return t("favorites.typeDocument");
    if (reminder.object_type === "case") return t("favorites.typeCase");
    return t("favorites.typeFolder");
  }

  function renderRow(entry: ResolvedReminder, kind: "due" | "upcoming" | "done") {
    const { reminder, document, folder, case: caseItem } = entry;
    const name = document?.title ?? folder?.name ?? caseItem?.name ?? t("favorites.unresolvedName");
    const busy = busyId === reminder.id;
    const dueLabel = formatDueAt(reminder.due_at, locale);
    // Three levels (when and what, the note, the actions) stacked, so a long
    // name or note wraps instead of colliding with the buttons at any width.
    return (
      <li className="flex flex-col gap-2 border-b border-border py-3" key={reminder.id}>
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className={kind === "due" ? "badge warn" : "badge neutral"}>{dueLabel}</span>
          <span className="min-w-0 break-words">
            {typeLabel(reminder)} {name}
          </span>
        </div>
        {reminder.note && <p className="m-0 break-words text-sm opacity-80">{reminder.note}</p>}
        <div className="flex flex-wrap gap-2">
          {document && (
            <button type="button" className={secondaryBtn} onClick={() => onOpenDocument(document)}>
              {t("favorites.open")}
            </button>
          )}
          {folder && (
            <button type="button" className={secondaryBtn} onClick={() => onOpenFolder(folder)}>
              {t("favorites.open")}
            </button>
          )}
          {caseItem && (
            <button type="button" className={secondaryBtn} onClick={() => onOpenCase(caseItem)}>
              {t("favorites.open")}
            </button>
          )}
          {kind === "done" ? (
            <button
              type="button"
              className={secondaryBtn}
              disabled={busy}
              onClick={() => run(reminder, () => reopenReminder(token, reminder.id))}
            >
              {t("reminders.reopen")}
            </button>
          ) : (
            <>
              <button
                type="button"
                className={secondaryBtn}
                disabled={busy}
                onClick={() => run(reminder, () => completeReminder(token, reminder.id))}
              >
                {t("reminders.done")}
              </button>
              {SNOOZE_PRESETS.map((preset) => (
                <button
                  key={preset}
                  type="button"
                  className={secondaryBtn}
                  disabled={busy}
                  aria-label={t(`reminders.snooze.${preset}Label`, { name })}
                  onClick={() =>
                    run(reminder, () => snoozeReminder(token, reminder.id, presetDate(preset).toISOString()))
                  }
                >
                  {t(`reminders.snooze.${preset}`)}
                </button>
              ))}
            </>
          )}
          <button
            type="button"
            className={secondaryBtn}
            disabled={busy}
            onClick={() => run(reminder, () => deleteReminder(token, reminder.id))}
          >
            {t("reminders.delete")}
          </button>
        </div>
      </li>
    );
  }

  const due = open.filter((entry) => entry.reminder.is_due);
  const upcoming = open.filter((entry) => !entry.reminder.is_due);

  return (
    <section className="favorites-pane" aria-label={t("reminders.paneLabel")}>
      <h2 className="m-0 mb-3 text-base">{t("reminders.heading")}</h2>
      <p className="text-sm opacity-80">{t("reminders.hint")}</p>

      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : (
        <>
          <section aria-labelledby="reminders-due-heading">
            <h3 id="reminders-due-heading" className="mb-1 mt-4 text-sm">
              {t("reminders.dueHeading", { count: due.length })}
            </h3>
            {due.length === 0 ? (
              <p className="italic opacity-70">{t("reminders.dueEmpty")}</p>
            ) : (
              <ul className="m-0 list-none p-0">{due.map((entry) => renderRow(entry, "due"))}</ul>
            )}
          </section>
          <section aria-labelledby="reminders-upcoming-heading">
            <h3 id="reminders-upcoming-heading" className="mb-1 mt-4 text-sm">
              {t("reminders.upcomingHeading", { count: upcoming.length })}
            </h3>
            {upcoming.length === 0 ? (
              <p className="italic opacity-70">{t("reminders.upcomingEmpty")}</p>
            ) : (
              <ul className="m-0 list-none p-0">{upcoming.map((entry) => renderRow(entry, "upcoming"))}</ul>
            )}
          </section>
          {done.length > 0 && (
            <details className="mt-4">
              <summary className="cursor-pointer text-sm">
                {t("reminders.doneHeading", { count: done.length })}
              </summary>
              <ul className="m-0 list-none p-0">{done.map((entry) => renderRow(entry, "done"))}</ul>
            </details>
          )}
        </>
      )}
    </section>
  );
}
