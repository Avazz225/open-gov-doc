"use client";

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  type Sensor,
  type SensorConfig,
  type TracingConfig,
  getSensorConfig,
  getTracingConfig,
  setTracingSamplingRatio,
  listAlertRules,
  listDowntimeWindows,
  type AlertRule,
  type DowntimeWindow,
  listSensors,
  setSensorGlobalDefault,
  setSensorOverride,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import {
  ActiveAlerts,
  DowntimeWindows,
  LevelBadge,
  SensorAlertRules,
  worstLevel,
} from "@/components/SensorAlerts";
import { BoundedList } from "@/components/BoundedList";
import { usePrincipalName } from "@/components/PrincipalName";
import { LogLevelSettings } from "@/components/LogLevelSettings";

// Monitoring sensors (P88-S2, ADR 0243, concept 10.1): the global base
// setting ("monitor everything"/"nothing"), switches per cost class and per
// group, and per sensor. The most specific setting wins: sensor, then
// group, then cost class, then the global default - the same rule the
// services apply themselves (`dms_metrics_client.resolve_active`), so a
// sensor shown as off really generates no data. Editing needs
// `admin.monitoring`; every change is an audit event.

type Choice = "inherit" | "on" | "off";

// P88-S2c: cost is a level from 1 (very cheap) to 5 (very expensive).
const COST_LEVELS = [1, 2, 3, 4, 5] as const;

// Five dots, the first `level` filled, in the level's colour from a
// sequential green-to-red scale (globals.css `--dms-cost-<n>`). The dot
// count carries the level on its own, and the label says it in words, so
// colour is never the only carrier (colour-vision deficiency, high
// contrast).
export function CostDots({ level, label }: { level: number; label: string }) {
  return (
    <span className="cost-dots" data-level={level} role="img" aria-label={label} title={label}>
      {COST_LEVELS.map((i) => (
        <span key={i} className={i <= level ? "cost-dot filled" : "cost-dot"} aria-hidden="true" />
      ))}
    </span>
  );
}

function choiceOf(overrides: Record<string, boolean>, key: string): Choice {
  if (!(key in overrides)) return "inherit";
  return overrides[key] ? "on" : "off";
}

const fieldInput =
  "box-border rounded-md border border-border bg-bg px-2 py-1 text-sm text-fg outline-none focus:border-accent focus:ring-2 focus:ring-accent-bg disabled:opacity-60";
const card = "rounded-lg border border-border p-4 mb-6";

export function SensorSettings() {
  const { accessToken, permissions } = useAuth();
  const { t } = useI18n();
  const canManage = permissions.includes("admin.monitoring");
  const [sensors, setSensors] = useState<Sensor[]>([]);
  const [config, setConfig] = useState<SensorConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState("");
  // Alerting (P88-S6, ADR 0245).
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [windows, setWindows] = useState<DowntimeWindow[]>([]);
  const [expandedSensor, setExpandedSensor] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setError(null);
    try {
      const [loadedSensors, loadedConfig, loadedRules, loadedWindows] = await Promise.all([
        listSensors(accessToken),
        getSensorConfig(accessToken),
        listAlertRules(accessToken),
        listDowntimeWindows(accessToken),
      ]);
      setSensors(loadedSensors);
      setConfig(loadedConfig);
      setRules(loadedRules);
      setWindows(loadedWindows);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [accessToken, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function run(action: () => Promise<unknown>) {
    if (!accessToken) return;
    setBusy(true);
    setError(null);
    try {
      await action();
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.actionError"));
    } finally {
      setBusy(false);
    }
  }

  function setChoice(key: string, choice: Choice) {
    const enabled = choice === "inherit" ? null : choice === "on";
    run(() => setSensorOverride(accessToken ?? "", key, enabled));
  }

  const groups = useMemo(() => {
    const byGroup = new Map<string, Sensor[]>();
    for (const sensor of sensors) {
      byGroup.set(sensor.group, [...(byGroup.get(sensor.group) ?? []), sensor]);
    }
    return [...byGroup.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [sensors]);

  // Overrides whose sensor or group no running service declares any more
  // (a stopped service, a renamed sensor): kept by the server, listed here
  // so they can be reset instead of lingering invisibly.
  const orphaned = useMemo(() => {
    if (!config) return [];
    const names = new Set(sensors.map((s) => s.name));
    const groupNames = new Set(sensors.map((s) => s.group));
    return Object.keys(config.overrides)
      .filter((key) => {
        if (key.startsWith("cost:")) return false;
        if (key.startsWith("group:")) return !groupNames.has(key.slice("group:".length));
        return !names.has(key);
      })
      .sort();
  }, [config, sensors]);

  const rulesBySensor = useMemo(() => {
    const map = new Map<string, AlertRule[]>();
    for (const rule of rules) map.set(rule.sensor, [...(map.get(rule.sensor) ?? []), rule]);
    return map;
  }, [rules]);

  const groupLabel = (group: string) => {
    const key = `sensors.groups.${group}`;
    const label = t(key);
    return label === key ? group : label;
  };

  if (isLoading) return <p>{t("common.loading")}</p>;
  if (!config) return <ErrorLine error={error} />;

  const overrides = config.overrides;
  const activeCount = sensors.filter((s) => s.active).length;
  const needle = filter.trim().toLowerCase();
  const matches = (s: Sensor) =>
    !needle ||
    s.name.toLowerCase().includes(needle) ||
    s.description.toLowerCase().includes(needle) ||
    s.service_types.some((type) => type.toLowerCase().includes(needle));

  const choiceSelect = (settingKey: string, label: string) => (
      <select
        aria-label={label}
        value={choiceOf(overrides, settingKey)}
        onChange={(e) => setChoice(settingKey, e.target.value as Choice)}
        disabled={!canManage || busy}
        className={fieldInput}
      >
        <option value="inherit">{t("sensors.choice.inherit")}</option>
        <option value="on">{t("sensors.choice.on")}</option>
        <option value="off">{t("sensors.choice.off")}</option>
      </select>
  );

  return (
    <>
      <ErrorLine error={error} />
      {!canManage && <p className="text-sm opacity-80">{t("sensors.readOnly")}</p>}

      <ActiveAlerts rules={rules} />

      <section className={card} aria-labelledby="sensors-global-heading">
        <h2 id="sensors-global-heading">{t("sensors.global.heading")}</h2>
        <p className="text-sm opacity-80">{t("sensors.global.hint")}</p>
        <div className="flex flex-wrap items-center gap-3" role="radiogroup" aria-label={t("sensors.global.heading")}>
          {[true, false].map((value) => (
            <label key={String(value)} className="flex items-center gap-1">
              <input
                type="radio"
                name="sensor-global-default"
                checked={config.global_default === value}
                disabled={!canManage || busy}
                onChange={() => run(() => setSensorGlobalDefault(accessToken ?? "", value))}
              />
              {value ? t("sensors.global.everything") : t("sensors.global.nothing")}
            </label>
          ))}
          <span className="text-sm opacity-80">
            {t("sensors.activeCount", { active: activeCount, total: sensors.length })}
          </span>
        </div>
      </section>

      <section className={card} aria-labelledby="sensors-cost-heading">
        <h2 id="sensors-cost-heading">{t("sensors.cost.heading")}</h2>
        <p className="text-sm opacity-80">{t("sensors.cost.hint")}</p>
        <div className="flex flex-wrap gap-6">
          {COST_LEVELS.map((level) => {
            const ofCost = sensors.filter((s) => s.cost === level);
            const label = t("sensors.costLevel", { level, name: t(`sensors.costs.${level}`) });
            return (
              <div key={level} className="flex flex-col gap-1 text-sm">
                <span className="flex items-center gap-2 whitespace-nowrap">
                  <CostDots level={level} label={label} />
                  {t(`sensors.costs.${level}`)}
                </span>
                <span className="text-xs opacity-70">
                  {t("sensors.activeCount", {
                    active: ofCost.filter((s) => s.active).length,
                    total: ofCost.length,
                  })}
                </span>
                {choiceSelect(`cost:${level}`, t("sensors.costSetting", { level }))}
              </div>
            );
          })}
        </div>
      </section>

      <TracingSection canManage={canManage} />
      <LogLevelSettings canManage={canManage} />

      <section className={card} aria-labelledby="sensors-list-heading">
        <h2 id="sensors-list-heading">{t("sensors.list.heading")}</h2>
        <p className="text-sm opacity-80">{t("sensors.list.hint")}</p>
        <label className="mb-3 flex flex-col gap-1 text-sm">
          {t("sensors.list.filter")}
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className={`${fieldInput} max-w-sm`}
          />
        </label>
        {sensors.length === 0 ? (
          <p className="italic opacity-70">{t("sensors.list.empty")}</p>
        ) : (
          <BoundedList label={t("sensors.list.heading")} total={sensors.length}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t("sensors.list.sensor")}</th>
                  <th>{t("sensors.list.cost")}</th>
                  <th>{t("sensors.list.services")}</th>
                  <th>{t("sensors.list.state")}</th>
                  <th>{t("alerts.column")}</th>
                  <th>{t("sensors.list.setting")}</th>
                </tr>
              </thead>
              <tbody>
                {groups.map(([group, members]) => {
                  const visible = members.filter(matches);
                  if (visible.length === 0) return null;
                  const groupWorst = worstLevel(members.flatMap((m) => rulesBySensor.get(m.name) ?? []));
                  return (
                    <Fragment key={group}>
                      <tr className="bg-hover-bg">
                        <th scope="rowgroup" colSpan={3} className="text-left">
                          {groupLabel(group)}{" "}
                          <span className="font-normal opacity-70">
                            ({t("sensors.activeCount", {
                              active: members.filter((s) => s.active).length,
                              total: members.length,
                            })})
                          </span>
                        </th>
                        <td />
                        <td>{groupWorst && <LevelBadge level={groupWorst} />}</td>
                        <td>
                          {choiceSelect(`group:${group}`, t("sensors.list.groupSetting", { group: groupLabel(group) }))}
                        </td>
                      </tr>
                      {visible.map((sensor) => {
                        const sensorRules = rulesBySensor.get(sensor.name) ?? [];
                        const sensorWorst = worstLevel(sensorRules);
                        const isOpen = expandedSensor === sensor.name;
                        return (
                        <Fragment key={sensor.name}>
                        <tr>
                          <td>
                            <code className="font-mono text-xs">{sensor.name}</code>
                            {!sensor.default_enabled && (
                              <span className="badge neutral ml-2">{t("sensors.defaultOff")}</span>
                            )}
                            <div className="text-sm opacity-80">{sensor.description}</div>
                          </td>
                          <td>
                            <span className="flex items-center gap-2 whitespace-nowrap text-sm">
                              <CostDots
                                level={sensor.cost}
                                label={t("sensors.costLevel", {
                                  level: sensor.cost,
                                  name: t(`sensors.costs.${sensor.cost}`),
                                })}
                              />
                              <span className="opacity-80">{t(`sensors.costs.${sensor.cost}`)}</span>
                            </span>
                          </td>
                          <td className="text-sm">
                            {/* Shared sensors (the HTTP middleware's) come from
                                every service: collapsed to a count. */}
                            {sensor.service_types.length > 3 ? (
                              <details>
                                <summary className="cursor-pointer whitespace-nowrap">
                                  {t("sensors.list.serviceCount", { count: sensor.service_types.length })}
                                </summary>
                                {sensor.service_types.join(", ")}
                              </details>
                            ) : (
                              sensor.service_types.join(", ")
                            )}
                          </td>
                          <td className="whitespace-nowrap">
                            <span className={`badge ${sensor.active ? "ok" : "neutral"}`}>
                              {sensor.active ? t("sensors.on") : t("sensors.off")}
                            </span>
                            <div className="text-xs opacity-70">{t(`sensors.source.${sensor.source}`)}</div>
                          </td>
                          <td className="whitespace-nowrap">
                            {sensorWorst && <LevelBadge level={sensorWorst} />}
                            {(sensorRules.length > 0 || canManage) && (
                              <button
                                type="button"
                                className="ml-1 border-0 bg-transparent p-0 text-xs text-accent underline"
                                aria-expanded={isOpen}
                                aria-label={t("alerts.toggle", { sensor: sensor.name })}
                                onClick={() => setExpandedSensor(isOpen ? null : sensor.name)}
                              >
                                {sensorRules.length > 0
                                  ? t("alerts.ruleCount", { count: sensorRules.length })
                                  : t("alerts.setUp")}
                              </button>
                            )}
                          </td>
                          <td>
                            {choiceSelect(sensor.name, t("sensors.list.sensorSetting", { name: sensor.name }))}
                          </td>
                        </tr>
                        {isOpen && accessToken && (
                          <tr>
                            <td colSpan={6} className="bg-hover-bg">
                              <SensorAlertRules
                                token={accessToken}
                                sensor={sensor.name}
                                sensorActive={sensor.active}
                                rules={sensorRules}
                                canManage={canManage}
                                onChange={reload}
                              />
                            </td>
                          </tr>
                        )}
                        </Fragment>
                        );
                      })}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </BoundedList>
        )}
      </section>

      {accessToken && (
        <DowntimeWindows
          token={accessToken}
          windows={windows}
          rules={rules}
          canManage={canManage}
          onChange={reload}
        />
      )}

      {orphaned.length > 0 && (
        <section className={card} aria-labelledby="sensors-orphaned-heading">
          <h2 id="sensors-orphaned-heading">{t("sensors.orphaned.heading")}</h2>
          <p className="text-sm opacity-80">{t("sensors.orphaned.hint")}</p>
          <ul className="flex list-none flex-col gap-2 p-0">
            {orphaned.map((key) => (
              <li key={key} className="flex flex-wrap items-center gap-2">
                <code className="font-mono text-xs">{key}</code>
                <span className="text-sm">{overrides[key] ? t("sensors.on") : t("sensors.off")}</span>
                {canManage && (
                  <button
                    type="button"
                    className="rounded-md border border-border bg-hover-bg px-3 py-1 text-sm text-fg hover:bg-accent-bg disabled:opacity-50"
                    disabled={busy}
                    onClick={() => setChoice(key, "inherit")}
                  >
                    {t("sensors.orphaned.reset")}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function ErrorLine({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <p className="text-danger" role="alert">
      {error}
    </p>
  );
}

// Distributed tracing (P94-S1, ADR 0250): what share of new requests is
// traced across every service. 100 % suits a dev stack or a short
// investigation; a production installation keeps a few per cent.
function TracingSection({ canManage }: { canManage: boolean }) {
  const { accessToken } = useAuth();
  const { t, locale } = useI18n();
  const [config, setConfig] = useState<TracingConfig | null>(null);
  const [percent, setPercent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  // Since P85 the actor is a principal id; show the person's name.
  const changedBy = usePrincipalName(config?.updated_by);

  useEffect(() => {
    if (!accessToken) return;
    getTracingConfig(accessToken)
      .then((loaded) => {
        setConfig(loaded);
        setPercent(String(Math.round(loaded.sampling_ratio * 1000) / 10));
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : t("common.loadError")));
  }, [accessToken, t]);

  const value = Number(percent.replace(",", "."));
  const valid = percent.trim() !== "" && Number.isFinite(value) && value >= 0 && value <= 100;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!accessToken || !valid) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      setConfig(await setTracingSamplingRatio(accessToken, value / 100));
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.actionError"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={card} aria-labelledby="sensors-tracing-heading">
      <h2 id="sensors-tracing-heading">{t("sensors.tracing.heading")}</h2>
      <p className="text-sm opacity-80">{t("sensors.tracing.hint")}</p>
      <ErrorLine error={error} />
      {config && (
        <>
          {!config.tracing_enabled && (
            <p className="text-sm italic opacity-80">{t("sensors.tracing.notConfigured")}</p>
          )}
          <form className="flex flex-wrap items-end gap-3" onSubmit={save}>
            <div className="flex flex-col gap-1 text-sm">
              <label htmlFor="tracing-ratio">{t("sensors.tracing.ratio")}</label>
              <span className="flex items-center gap-1">
                <input
                  id="tracing-ratio"
                  inputMode="decimal"
                  className={`${fieldInput} w-24`}
                  value={percent}
                  disabled={!canManage || busy}
                  onChange={(e) => {
                    setPercent(e.target.value);
                    setSaved(false);
                  }}
                  aria-invalid={!valid}
                />
                <span aria-hidden="true">%</span>
              </span>
            </div>
            {canManage && (
              <button
                type="submit"
                disabled={!valid || busy}
                className="rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap"
              >
                {t("sensors.tracing.save")}
              </button>
            )}
          </form>
          {!valid && <p className="text-sm text-danger">{t("sensors.tracing.invalid")}</p>}
          {saved && (
            <p role="status" className="text-sm">
              {t("sensors.tracing.saved")}
            </p>
          )}
          {config.updated_at && (
            <p className="text-xs opacity-70" title={changedBy.title}>
              {t("sensors.tracing.lastChange", {
                who: changedBy.name || "?",
                when: new Date(config.updated_at).toLocaleString(locale),
              })}
            </p>
          )}
        </>
      )}
    </section>
  );
}
