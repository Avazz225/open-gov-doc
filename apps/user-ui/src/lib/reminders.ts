// Follow-up reminders (P90-S2, ADR 0246): date helpers shared by the
// reminder dialog and the "Wiedervorlagen" view, plus the change signal the
// icon rail's due badge listens to.

// A reminder is due at a point in time; the dialog works in the user's local
// time and sends ISO with "Z". Presets land at 09:00 local, the start of a
// working day.
export const DEFAULT_REMINDER_TIME = "09:00";

export type ReminderPreset = "tomorrow" | "nextWeek" | "nextMonth";

export function presetDate(preset: ReminderPreset, from: Date = new Date()): Date {
  const date = new Date(from);
  if (preset === "tomorrow") date.setDate(date.getDate() + 1);
  if (preset === "nextWeek") date.setDate(date.getDate() + 7);
  if (preset === "nextMonth") date.setMonth(date.getMonth() + 1);
  const [hours, minutes] = DEFAULT_REMINDER_TIME.split(":").map(Number);
  date.setHours(hours, minutes, 0, 0);
  return date;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

// Values for `<input type="date">`/`<input type="time">` in local time
// (`toISOString` would shift the day near midnight).
export function toDateInput(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function toTimeInput(date: Date): string {
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// Local date and time inputs to an ISO instant; null when incomplete.
export function localInputsToIso(dateInput: string, timeInput: string): string | null {
  if (!dateInput) return null;
  const date = new Date(`${dateInput}T${timeInput || DEFAULT_REMINDER_TIME}`);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

export const REMINDERS_CHANGED_EVENT = "dms:reminders-changed";

export function announceRemindersChanged(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(REMINDERS_CHANGED_EVENT));
}

// A reminder's due time in the app's language (not the browser's), short.
export function formatDueAt(iso: string, locale: string): string {
  return new Date(iso).toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
}
