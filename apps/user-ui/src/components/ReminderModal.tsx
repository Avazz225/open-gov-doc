"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  completeReminder,
  createReminder,
  deleteReminder,
  listReminders,
  type FavoriteObjectType,
  type Reminder,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import {
  announceRemindersChanged,
  formatDueAt,
  localInputsToIso,
  presetDate,
  toDateInput,
  toTimeInput,
  type ReminderPreset,
} from "@/lib/reminders";

const PRESETS: ReminderPreset[] = ["tomorrow", "nextWeek", "nextMonth"];

// Follow-up reminder dialog (Wiedervorlage, P90-S2, ADR 0246) - opened from
// the document/folder context menus, the document preview and the case
// detail. Sets a new reminder (date, time, note; presets for the common
// cases) and lists the open ones already on this object, so a second
// reminder is a deliberate choice. Same `.modal-*` pattern as
// ShareLinkModal.tsx.
export function ReminderModal({
  objectType,
  objectId,
  objectName,
  onClose,
}: {
  objectType: FavoriteObjectType;
  objectId: string;
  objectName: string;
  onClose: () => void;
}) {
  const { accessToken, user } = useAuth();
  const { t, locale } = useI18n();
  const username = user?.username ?? "";
  const initial = presetDate("nextWeek");
  const [dateInput, setDateInput] = useState(toDateInput(initial));
  const [timeInput, setTimeInput] = useState(toTimeInput(initial));
  const [note, setNote] = useState("");
  const [existing, setExisting] = useState<Reminder[]>([]);
  const [isSaving, setIsSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!accessToken || !username) return;
    try {
      setExisting(
        await listReminders(accessToken, username, {
          status: "open",
          objectType,
          objectId,
        })
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("reminders.loadError"));
    }
  }, [accessToken, username, objectType, objectId, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  function applyPreset(preset: ReminderPreset) {
    const date = presetDate(preset);
    setDateInput(toDateInput(date));
    setTimeInput(toTimeInput(date));
  }

  async function handleSave() {
    const dueAt = localInputsToIso(dateInput, timeInput);
    if (!accessToken || !username || !dueAt) return;
    setIsSaving(true);
    setError(null);
    try {
      await createReminder(accessToken, {
        user_id: username,
        object_type: objectType,
        object_id: objectId,
        due_at: dueAt,
        note: note.trim(),
      });
      setNote("");
      setSavedAt(formatDueAt(dueAt, locale));
      announceRemindersChanged();
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("reminders.saveError"));
    } finally {
      setIsSaving(false);
    }
  }

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

  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
  const fieldInput =
    "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";

  const heading = t("reminders.dialogHeading", { name: objectName });
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-content"
        role="dialog"
        aria-modal="true"
        aria-label={heading}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 className="m-0 text-base">{heading}</h2>
          <button type="button" className="modal-close" aria-label={t("common.close")} onClick={onClose}>
            ×
          </button>
        </div>

        <div className="flex flex-wrap gap-2" role="group" aria-label={t("reminders.presets")}>
          {PRESETS.map((preset) => (
            <button key={preset} type="button" className={secondaryBtn} onClick={() => applyPreset(preset)}>
              {t(`reminders.preset.${preset}`)}
            </button>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap gap-3">
          <label className="flex flex-col gap-1 text-sm">
            {t("reminders.date")}
            <input
              type="date"
              className={fieldInput}
              value={dateInput}
              onChange={(e) => setDateInput(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t("reminders.time")}
            <input
              type="time"
              className={fieldInput}
              value={timeInput}
              onChange={(e) => setTimeInput(e.target.value)}
            />
          </label>
        </div>
        <label className="mt-3 flex flex-col gap-1 text-sm">
          {t("reminders.note")}
          <textarea
            className={`${fieldInput} min-h-[4.5rem] w-full`}
            maxLength={2000}
            value={note}
            placeholder={t("reminders.notePlaceholder")}
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            type="button"
            className={primaryBtn}
            onClick={handleSave}
            disabled={isSaving || !localInputsToIso(dateInput, timeInput)}
          >
            {isSaving ? t("common.loading") : t("reminders.save")}
          </button>
          {savedAt && (
            <span className="text-sm opacity-80" role="status">
              {t("reminders.saved", { date: savedAt })}
            </span>
          )}
        </div>

        {existing.length > 0 && (
          <>
            <h3 className="mb-1 mt-4 text-sm">{t("reminders.existingHeading")}</h3>
            <ul className="m-0 list-none p-0">
              {existing.map((reminder) => (
                <li
                  className="flex flex-wrap items-center justify-between gap-2 border-b border-border py-2"
                  key={reminder.id}
                >
                  <span className="min-w-0 break-words">
                    {formatDueAt(reminder.due_at, locale)}
                    {reminder.note && <span className="block text-sm opacity-80">{reminder.note}</span>}
                  </span>
                  <span className="flex gap-2">
                    <button
                      type="button"
                      className={secondaryBtn}
                      disabled={busyId === reminder.id}
                      onClick={() => run(reminder, () => completeReminder(accessToken ?? "", reminder.id))}
                    >
                      {t("reminders.done")}
                    </button>
                    <button
                      type="button"
                      className={secondaryBtn}
                      disabled={busyId === reminder.id}
                      onClick={() => run(reminder, () => deleteReminder(accessToken ?? "", reminder.id))}
                    >
                      {t("reminders.delete")}
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}

        {error && (
          <p className="text-danger" role="alert">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
