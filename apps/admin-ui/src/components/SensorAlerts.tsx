"use client";

import { useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  createAlertRule,
  deleteAlertRule,
  deleteDowntimeWindow,
  previewAlertRule,
  saveDowntimeWindow,
  updateAlertRule,
  type AlertAggregation,
  type AlertLevel,
  type AlertPreview,
  type AlertRule,
  type AlertRuleInput,
  type DowntimeWindow,
  type DowntimeWindowInput,
} from "@/lib/api";

// Alerting UI on the sensors page (P88-S6, ADR 0245): level badges, the
// rule editor with a live preview, the active-alarm overview and the
// downtime windows. The engine is monitoring-service (P88-S5); this file
// only edits and shows. Writes need `admin.monitoring` - without it every
// control here is read-only.

const LEVEL_BADGE: Record<AlertLevel, string> = {
  ok: "ok",
  warning: "warn",
  error: "down",
  unknown: "neutral",
};

const LEVEL_RANK: Record<AlertLevel, number> = { unknown: 0, ok: 1, warning: 2, error: 3 };

/** The worst level of some rules (error > warning > ok > unknown), or null without rules. */
export function worstLevel(rules: AlertRule[]): AlertLevel | null {
  const enabled = rules.filter((r) => r.enabled);
  if (enabled.length === 0) return null;
  return enabled.reduce<AlertLevel>(
    (worst, r) => (LEVEL_RANK[r.level] > LEVEL_RANK[worst] ? r.level : worst),
    "unknown"
  );
}

export function LevelBadge({ level }: { level: AlertLevel }) {
  const { t } = useI18n();
  return <span className={`badge ${LEVEL_BADGE[level]}`}>{t(`alerts.levels.${level}`)}</span>;
}

export function formatValue(value: number | null): string {
  if (value === null) return "—";
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

/** "Warnung > 80 · Fehler > 95", the thresholds in one line. */
export function thresholdsSummary(rule: AlertRuleInput, t: (k: string) => string): string {
  const parts: string[] = [];
  if (rule.min_error !== null) parts.push(`${t("alerts.levels.error")} < ${rule.min_error}`);
  if (rule.min_warning !== null) parts.push(`${t("alerts.levels.warning")} < ${rule.min_warning}`);
  if (rule.max_warning !== null) parts.push(`${t("alerts.levels.warning")} > ${rule.max_warning}`);
  if (rule.max_error !== null) parts.push(`${t("alerts.levels.error")} > ${rule.max_error}`);
  return parts.join(" · ");
}

const primaryBtn =
  "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
const secondaryBtn =
  "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
const fieldInput =
  "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none focus:border-accent focus:ring-2 focus:ring-accent-bg";

const AGGREGATIONS: AlertAggregation[] = ["max", "min", "sum", "avg", "rate_per_minute"];

type ThresholdKey = "min_error" | "min_warning" | "max_warning" | "max_error";
const THRESHOLD_KEYS: ThresholdKey[] = ["min_error", "min_warning", "max_warning", "max_error"];

type RuleDraft = {
  name: string;
  aggregation: AlertAggregation;
  filter: [string, string][];
  thresholds: Record<ThresholdKey, string>;
  warningRepeat: string;
  errorRepeat: string;
  enabled: boolean;
  description: string;
};

function draftFrom(sensor: string, rule?: AlertRule): RuleDraft {
  return {
    name: rule?.name ?? sensor,
    aggregation: rule?.aggregation ?? "max",
    filter: Object.entries(rule?.label_filter ?? {}),
    thresholds: Object.fromEntries(
      THRESHOLD_KEYS.map((k) => [k, rule && rule[k] !== null ? String(rule[k]) : ""])
    ) as Record<ThresholdKey, string>,
    warningRepeat: String(rule?.warning_repeat ?? 1),
    errorRepeat: String(rule?.error_repeat ?? 1),
    enabled: rule?.enabled ?? true,
    description: rule?.description ?? "",
  };
}

export function toRuleInput(sensor: string, draft: RuleDraft): AlertRuleInput {
  const num = (v: string) => (v.trim() === "" ? null : Number(v));
  return {
    name: draft.name.trim(),
    sensor,
    label_filter: Object.fromEntries(draft.filter.filter(([k, v]) => k.trim() && v.trim())),
    aggregation: draft.aggregation,
    min_error: num(draft.thresholds.min_error),
    min_warning: num(draft.thresholds.min_warning),
    max_warning: num(draft.thresholds.max_warning),
    max_error: num(draft.thresholds.max_error),
    warning_repeat: Number(draft.warningRepeat) || 1,
    error_repeat: Number(draft.errorRepeat) || 1,
    enabled: draft.enabled,
    description: draft.description.trim() || null,
  };
}

/** Client checks mirroring the server's `validate_rule` - named before saving. */
export function ruleProblem(input: AlertRuleInput): string | null {
  const values = THRESHOLD_KEYS.map((k) => input[k]).filter((v): v is number => v !== null);
  if (values.length === 0) return "alerts.problems.noThreshold";
  if (values.some((v) => Number.isNaN(v))) return "alerts.problems.notANumber";
  if (values.some((v, i) => i > 0 && v < values[i - 1])) return "alerts.problems.order";
  if (input.warning_repeat < 1 || input.error_repeat < 1) return "alerts.problems.repeat";
  return null;
}

export function AlertRuleForm({
  token,
  sensor,
  sensorActive,
  rule,
  onDone,
}: {
  token: string;
  sensor: string;
  sensorActive: boolean;
  rule?: AlertRule;
  onDone: () => void;
}) {
  const { t } = useI18n();
  const [draft, setDraft] = useState<RuleDraft>(() => draftFrom(sensor, rule));
  const [preview, setPreview] = useState<AlertPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const patch = (next: Partial<RuleDraft>) => {
    setDraft((prev) => ({ ...prev, ...next }));
    setPreview(null);
  };

  async function runPreview() {
    setError(null);
    try {
      setPreview(await previewAlertRule(token, toRuleInput(sensor, draft)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.actionError"));
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const input = toRuleInput(sensor, draft);
    const problem = ruleProblem(input);
    if (problem) {
      setError(t(problem));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (rule) await updateAlertRule(token, rule.id, input);
      else await createAlertRule(token, input);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.actionError"));
    } finally {
      setBusy(false);
    }
  }

  function addFilter(key: string, value: string) {
    if (draft.filter.some(([k]) => k === key)) {
      patch({ filter: draft.filter.map(([k, v]) => (k === key ? [k, value] : [k, v])) });
    } else {
      patch({ filter: [...draft.filter, [key, value]] });
    }
  }

  return (
    <form
      aria-label={rule ? t("alerts.form.editLabel", { name: rule.name }) : t("alerts.form.newLabel", { sensor })}
      className="flex flex-col gap-3 rounded-md border border-border p-3"
      onSubmit={save}
    >
      {!sensorActive && <p className="m-0 text-sm text-danger">{t("alerts.form.sensorOff")}</p>}
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          {t("alerts.form.name")}
          <input value={draft.name} onChange={(e) => patch({ name: e.target.value })} required className={fieldInput} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          {t("alerts.form.aggregation")}
          <select
            value={draft.aggregation}
            onChange={(e) => patch({ aggregation: e.target.value as AlertAggregation })}
            className={fieldInput}
          >
            {AGGREGATIONS.map((a) => (
              <option key={a} value={a}>
                {t(`alerts.aggregations.${a}`)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <fieldset className="m-0 rounded-md border border-border p-2">
        <legend className="px-1 text-sm">{t("alerts.form.filter")}</legend>
        {draft.filter.length === 0 && <p className="m-0 text-xs opacity-70">{t("alerts.form.noFilter")}</p>}
        {draft.filter.map(([key, value], index) => (
          <div key={index} className="mb-1 flex flex-wrap items-center gap-2">
            <input
              aria-label={t("alerts.form.filterKey", { index: index + 1 })}
              value={key}
              onChange={(e) =>
                patch({ filter: draft.filter.map((f, i) => (i === index ? [e.target.value, f[1]] : f)) })
              }
              className={`${fieldInput} w-32 font-mono`}
            />
            =
            <input
              aria-label={t("alerts.form.filterValue", { index: index + 1 })}
              value={value}
              onChange={(e) =>
                patch({ filter: draft.filter.map((f, i) => (i === index ? [f[0], e.target.value] : f)) })
              }
              className={`${fieldInput} w-40 font-mono`}
            />
            <button
              type="button"
              className={secondaryBtn}
              aria-label={t("alerts.form.removeFilter", { index: index + 1 })}
              onClick={() => patch({ filter: draft.filter.filter((_, i) => i !== index) })}
            >
              ×
            </button>
          </div>
        ))}
        <button type="button" className={`${secondaryBtn} mt-1`} onClick={() => patch({ filter: [...draft.filter, ["", ""]] })}>
          {t("alerts.form.addFilter")}
        </button>
      </fieldset>

      <div className="flex flex-wrap items-end gap-3">
        {THRESHOLD_KEYS.map((key) => (
          <label key={key} className="flex flex-col gap-1 text-sm">
            {t(`alerts.thresholds.${key}`)}
            <input
              type="number"
              step="any"
              value={draft.thresholds[key]}
              onChange={(e) => patch({ thresholds: { ...draft.thresholds, [key]: e.target.value } })}
              className={`${fieldInput} w-28`}
            />
          </label>
        ))}
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          {t("alerts.form.warningRepeat")}
          <input
            type="number"
            min={1}
            value={draft.warningRepeat}
            onChange={(e) => patch({ warningRepeat: e.target.value })}
            className={`${fieldInput} w-20`}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          {t("alerts.form.errorRepeat")}
          <input
            type="number"
            min={1}
            value={draft.errorRepeat}
            onChange={(e) => patch({ errorRepeat: e.target.value })}
            className={`${fieldInput} w-20`}
          />
        </label>
        <label className="flex items-center gap-1 pb-2 text-sm">
          <input type="checkbox" checked={draft.enabled} onChange={(e) => patch({ enabled: e.target.checked })} />
          {t("alerts.form.enabled")}
        </label>
      </div>
      <p className="m-0 text-xs opacity-70">{t("alerts.form.repeatHint")}</p>

      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className={primaryBtn} disabled={busy}>
          {t("common.save")}
        </button>
        <button type="button" className={secondaryBtn} onClick={runPreview}>
          {t("alerts.form.preview")}
        </button>
        <button type="button" className={secondaryBtn} onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
      {error && (
        <p className="m-0 text-danger" role="alert">
          {error}
        </p>
      )}
      {preview && (
        <div role="status" className="text-sm">
          {preview.reason ? (
            <p className="m-0">{t(`alerts.preview.${preview.reason}`)}</p>
          ) : (
            <p className="m-0 flex items-center gap-2">
              {t("alerts.preview.current", { value: formatValue(preview.value) })}
              <LevelBadge level={preview.level} />
            </p>
          )}
          {preview.series.length > 0 && (
            <ul className="m-0 mt-1 list-none p-0">
              {preview.series.map((series, index) => (
                <li key={index} className="flex flex-wrap items-center gap-1 text-xs">
                  <span className="font-mono">{formatValue(series.value)}</span>
                  {Object.entries(series.labels).map(([k, v]) => (
                    <button
                      key={k}
                      type="button"
                      className="badge neutral cursor-pointer border-0"
                      title={t("alerts.preview.useAsFilter")}
                      onClick={() => addFilter(k, v)}
                    >
                      {k}={v}
                    </button>
                  ))}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </form>
  );
}

/** The rules of one sensor, in its expanded table row. */
export function SensorAlertRules({
  token,
  sensor,
  sensorActive,
  rules,
  canManage,
  onChange,
}: {
  token: string;
  sensor: string;
  sensorActive: boolean;
  rules: AlertRule[];
  canManage: boolean;
  onChange: () => Promise<void>;
}) {
  const { t, locale } = useI18n();
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function done() {
    setEditing(null);
    await onChange();
  }

  async function remove(rule: AlertRule) {
    if (!window.confirm(t("alerts.deleteConfirm", { name: rule.name }))) return;
    try {
      await deleteAlertRule(token, rule.id);
      await onChange();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.actionError"));
    }
  }

  return (
    <div className="flex flex-col gap-2" aria-label={t("alerts.rulesFor", { sensor })}>
      {error && (
        <p className="m-0 text-danger" role="alert">
          {error}
        </p>
      )}
      {rules.length === 0 && editing !== "new" && <p className="m-0 text-sm italic opacity-70">{t("alerts.noRules")}</p>}
      {rules.map((rule) =>
        editing === rule.id ? (
          <AlertRuleForm key={rule.id} token={token} sensor={sensor} sensorActive={sensorActive} rule={rule} onDone={done} />
        ) : (
          <div key={rule.id} className="flex flex-wrap items-center gap-2 text-sm">
            <LevelBadge level={rule.enabled ? rule.level : "unknown"} />
            <strong>{rule.name}</strong>
            {!rule.enabled && <span className="badge neutral">{t("alerts.disabled")}</span>}
            <span className="opacity-80">
              {t(`alerts.aggregations.${rule.aggregation}`)}
              {Object.keys(rule.label_filter).length > 0 &&
                ` (${Object.entries(rule.label_filter)
                  .map(([k, v]) => `${k}=${v}`)
                  .join(", ")})`}
              : {formatValue(rule.value)}
            </span>
            <span className="opacity-70">{thresholdsSummary(rule, t)}</span>
            {rule.since && (
              <span className="text-xs opacity-70">
                {t("alerts.since", { date: new Date(rule.since).toLocaleString(locale) })}
              </span>
            )}
            {rule.suppressed && <span className="badge neutral">{t("alerts.suppressed")}</span>}
            {canManage && (
              <span className="ml-auto flex gap-2">
                <button type="button" className={secondaryBtn} onClick={() => setEditing(rule.id)}>
                  {t("alerts.edit")}
                </button>
                <button type="button" className={secondaryBtn} onClick={() => remove(rule)}>
                  {t("common.delete")}
                </button>
              </span>
            )}
          </div>
        )
      )}
      {editing === "new" ? (
        <AlertRuleForm token={token} sensor={sensor} sensorActive={sensorActive} onDone={done} />
      ) : (
        canManage && (
          <button type="button" className={`${secondaryBtn} self-start`} onClick={() => setEditing("new")}>
            {t("alerts.addRule")}
          </button>
        )
      )}
    </div>
  );
}

/** Rules currently in warning/error, worst first. */
export function ActiveAlerts({ rules }: { rules: AlertRule[] }) {
  const { t, locale } = useI18n();
  const active = rules
    .filter((r) => r.enabled && (r.level === "warning" || r.level === "error"))
    .sort((a, b) => LEVEL_RANK[b.level] - LEVEL_RANK[a.level]);
  return (
    <section className="rounded-lg border border-border p-4 mb-6" aria-labelledby="alerts-active-heading">
      <h2 id="alerts-active-heading">{t("alerts.active.heading")}</h2>
      {active.length === 0 ? (
        <p className="m-0 text-sm opacity-80">
          {rules.length === 0 ? t("alerts.active.noRules") : t("alerts.active.none", { count: rules.length })}
        </p>
      ) : (
        // P91-S5: many rules can fire at once (an outage) - bounded.
        <ul className="m-0 flex max-h-[24rem] list-none flex-col gap-2 overflow-y-auto p-0">
          {active.map((rule) => (
            <li key={rule.id} className="flex flex-wrap items-center gap-2 text-sm">
              <LevelBadge level={rule.level} />
              <strong>{rule.name}</strong>
              <code className="font-mono text-xs">{rule.sensor}</code>
              <span>
                {formatValue(rule.value)} ({thresholdsSummary(rule, t)})
              </span>
              {rule.since && (
                <span className="text-xs opacity-70">
                  {t("alerts.since", { date: new Date(rule.since).toLocaleString(locale) })}
                </span>
              )}
              {rule.suppressed && <span className="badge neutral">{t("alerts.suppressed")}</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// --- Downtime windows -------------------------------------------------------------

const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6];

type WindowDraft = {
  name: string;
  startsAt: string;
  endsAt: string;
  weekdays: number[];
  dailyStart: string;
  dailyEnd: string;
  allRules: boolean;
  ruleIds: string[];
  enabled: boolean;
};

// <input type="datetime-local"> works in local time without a zone; the API
// wants ISO timestamps.
function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function windowDraft(w?: DowntimeWindow): WindowDraft {
  return {
    name: w?.name ?? "",
    startsAt: toLocalInput(w?.starts_at ?? null),
    endsAt: toLocalInput(w?.ends_at ?? null),
    weekdays: w?.weekdays ?? [],
    dailyStart: w?.daily_start ?? "",
    dailyEnd: w?.daily_end ?? "",
    allRules: !w?.rule_ids,
    ruleIds: w?.rule_ids ?? [],
    enabled: w?.enabled ?? true,
  };
}

export function toWindowInput(d: WindowDraft): DowntimeWindowInput {
  return {
    name: d.name.trim(),
    starts_at: d.startsAt ? new Date(d.startsAt).toISOString() : null,
    ends_at: d.endsAt ? new Date(d.endsAt).toISOString() : null,
    weekdays: d.weekdays.length > 0 ? [...d.weekdays].sort() : null,
    daily_start: d.dailyStart || null,
    daily_end: d.dailyEnd || null,
    rule_ids: d.allRules ? null : d.ruleIds,
    enabled: d.enabled,
  };
}

export function DowntimeWindows({
  token,
  windows,
  rules,
  canManage,
  onChange,
}: {
  token: string;
  windows: DowntimeWindow[];
  rules: AlertRule[];
  canManage: boolean;
  onChange: () => Promise<void>;
}) {
  const { t, locale } = useI18n();
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [draft, setDraft] = useState<WindowDraft>(windowDraft());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function open(w?: DowntimeWindow) {
    setError(null);
    setDraft(windowDraft(w));
    setEditing(w?.id ?? "new");
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await saveDowntimeWindow(token, toWindowInput(draft), editing === "new" ? undefined : (editing ?? undefined));
      setEditing(null);
      await onChange();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.actionError"));
    } finally {
      setBusy(false);
    }
  }

  async function remove(w: DowntimeWindow) {
    if (!window.confirm(t("alerts.windows.deleteConfirm", { name: w.name }))) return;
    try {
      await deleteDowntimeWindow(token, w.id);
      await onChange();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.actionError"));
    }
  }

  const schedule = (w: DowntimeWindow) => {
    const parts: string[] = [];
    if (w.starts_at || w.ends_at) {
      parts.push(
        `${w.starts_at ? new Date(w.starts_at).toLocaleString(locale) : "…"} – ${
          w.ends_at ? new Date(w.ends_at).toLocaleString(locale) : "…"
        }`
      );
    }
    if (w.weekdays && w.weekdays.length > 0) {
      parts.push(w.weekdays.map((d) => t(`alerts.windows.weekdays.${d}`)).join(", "));
    }
    if (w.daily_start && w.daily_end) parts.push(`${w.daily_start}–${w.daily_end}`);
    return parts.join(" · ");
  };

  return (
    <section className="rounded-lg border border-border p-4 mb-6" aria-labelledby="alerts-windows-heading">
      <h2 id="alerts-windows-heading">{t("alerts.windows.heading")}</h2>
      <p className="text-sm opacity-80">{t("alerts.windows.hint")}</p>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
      {windows.length === 0 ? (
        <p className="text-sm italic opacity-70">{t("alerts.windows.empty")}</p>
      ) : (
        <ul className="m-0 mb-3 flex max-h-[24rem] list-none flex-col gap-2 overflow-y-auto p-0">
          {windows.map((w) => (
            <li key={w.id} className="flex flex-wrap items-center gap-2 text-sm">
              <span className={`badge ${w.active ? "warn" : "neutral"}`}>
                {w.active ? t("alerts.windows.active") : w.enabled ? t("alerts.windows.inactive") : t("alerts.disabled")}
              </span>
              <strong>{w.name}</strong>
              <span className="opacity-80">{schedule(w)}</span>
              <span className="text-xs opacity-70">
                {w.rule_ids ? t("alerts.windows.someRules", { count: w.rule_ids.length }) : t("alerts.windows.allRules")}
              </span>
              {canManage && (
                <span className="ml-auto flex gap-2">
                  <button type="button" className={secondaryBtn} onClick={() => open(w)}>
                    {t("alerts.edit")}
                  </button>
                  <button type="button" className={secondaryBtn} onClick={() => remove(w)}>
                    {t("common.delete")}
                  </button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {canManage && editing === null && (
        <button type="button" className={secondaryBtn} onClick={() => open()}>
          {t("alerts.windows.add")}
        </button>
      )}
      {editing !== null && (
        <form
          aria-label={editing === "new" ? t("alerts.windows.newLabel") : t("alerts.windows.editLabel")}
          className="flex flex-col gap-3 rounded-md border border-border p-3"
          onSubmit={save}
        >
          <label className="flex flex-col gap-1 text-sm">
            {t("alerts.windows.name")}
            <input
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              required
              className={`${fieldInput} max-w-sm`}
            />
          </label>
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-sm">
              {t("alerts.windows.startsAt")}
              <input
                type="datetime-local"
                value={draft.startsAt}
                onChange={(e) => setDraft({ ...draft, startsAt: e.target.value })}
                className={fieldInput}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              {t("alerts.windows.endsAt")}
              <input
                type="datetime-local"
                value={draft.endsAt}
                onChange={(e) => setDraft({ ...draft, endsAt: e.target.value })}
                className={fieldInput}
              />
            </label>
          </div>
          <fieldset className="m-0 rounded-md border border-border p-2">
            <legend className="px-1 text-sm">{t("alerts.windows.weekly")}</legend>
            <div className="flex flex-wrap gap-3">
              {WEEKDAYS.map((d) => (
                <label key={d} className="flex items-center gap-1 text-sm">
                  <input
                    type="checkbox"
                    checked={draft.weekdays.includes(d)}
                    onChange={() =>
                      setDraft({
                        ...draft,
                        weekdays: draft.weekdays.includes(d) ? draft.weekdays.filter((x) => x !== d) : [...draft.weekdays, d],
                      })
                    }
                  />
                  {t(`alerts.windows.weekdays.${d}`)}
                </label>
              ))}
            </div>
            <div className="mt-2 flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1 text-sm">
                {t("alerts.windows.dailyStart")}
                <input
                  type="time"
                  value={draft.dailyStart}
                  onChange={(e) => setDraft({ ...draft, dailyStart: e.target.value })}
                  className={fieldInput}
                />
              </label>
              <label className="flex flex-col gap-1 text-sm">
                {t("alerts.windows.dailyEnd")}
                <input
                  type="time"
                  value={draft.dailyEnd}
                  onChange={(e) => setDraft({ ...draft, dailyEnd: e.target.value })}
                  className={fieldInput}
                />
              </label>
            </div>
            <p className="m-0 mt-1 text-xs opacity-70">{t("alerts.windows.weeklyHint")}</p>
          </fieldset>
          <fieldset className="m-0 rounded-md border border-border p-2">
            <legend className="px-1 text-sm">{t("alerts.windows.scope")}</legend>
            <label className="flex items-center gap-1 text-sm">
              <input
                type="checkbox"
                checked={draft.allRules}
                onChange={(e) => setDraft({ ...draft, allRules: e.target.checked })}
              />
              {t("alerts.windows.allRules")}
            </label>
            {!draft.allRules && (
              <div className="mt-1 flex max-h-48 flex-wrap gap-3 overflow-y-auto">
                {rules.map((rule) => (
                  <label key={rule.id} className="flex items-center gap-1 text-sm">
                    <input
                      type="checkbox"
                      checked={draft.ruleIds.includes(rule.id)}
                      onChange={() =>
                        setDraft({
                          ...draft,
                          ruleIds: draft.ruleIds.includes(rule.id)
                            ? draft.ruleIds.filter((x) => x !== rule.id)
                            : [...draft.ruleIds, rule.id],
                        })
                      }
                    />
                    {rule.name}
                  </label>
                ))}
              </div>
            )}
          </fieldset>
          <label className="flex items-center gap-1 text-sm">
            <input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} />
            {t("alerts.form.enabled")}
          </label>
          <div className="flex flex-wrap gap-3">
            <button type="submit" className={primaryBtn} disabled={busy}>
              {t("common.save")}
            </button>
            <button type="button" className={secondaryBtn} onClick={() => setEditing(null)}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
