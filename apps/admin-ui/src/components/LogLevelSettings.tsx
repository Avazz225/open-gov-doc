"use client";

import { useEffect, useMemo, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  type LogLevels,
  type LogLevelSetting,
  getLogLevels,
  resetLogLevel,
  setLogLevel,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { PrincipalName } from "@/components/PrincipalName";

// Central log levels (P97-S2, ADR 0267): global, per service, per logger.
// monitoring-service hands them out with the sensor configuration; every
// replica of a service applies a change with its next poll (15 s). DEBUG
// always expires (default 60 min, at most 24 h) - it can log personal data
// and costs volume. Without a setting a service runs on its DMS_LOG_LEVEL.

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;
// Minutes; 0 = no expiry (not offered for DEBUG).
const DURATIONS = [0, 15, 60, 240, 1440] as const;
const ALL = "";

const fieldInput =
  "box-border rounded-md border border-border bg-bg px-2 py-1 text-sm text-fg outline-none focus:border-accent focus:ring-2 focus:ring-accent-bg disabled:opacity-60";
const card = "rounded-lg border border-border p-4 mb-6";
const primaryButton =
  "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
const plainButton =
  "rounded-md border border-border bg-transparent px-2 py-1 text-sm text-fg hover:bg-hover-bg disabled:opacity-50";

function remainingMinutes(expiresAt: string | null, now: number): number | null {
  if (!expiresAt) return null;
  return Math.max(0, Math.round((new Date(expiresAt).getTime() - now) / 60000));
}

export function LogLevelSettings({ canManage }: { canManage: boolean }) {
  const { accessToken } = useAuth();
  const { t, locale } = useI18n();
  const [data, setData] = useState<LogLevels | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [service, setService] = useState<string>(ALL);
  const [logger, setLogger] = useState("");
  const [level, setLevel] = useState<string>("DEBUG");
  const [duration, setDuration] = useState<number>(60);

  useEffect(() => {
    if (!accessToken) return;
    getLogLevels(accessToken)
      .then(setData)
      .catch((err) => setError(err instanceof ApiError ? err.message : t("common.loadError")));
  }, [accessToken, t]);

  // DEBUG can't be set without an expiry; fall back to the default duration.
  useEffect(() => {
    if (level === "DEBUG" && duration === 0) setDuration(60);
  }, [level, duration]);

  const services = useMemo(() => Object.keys(data?.effective ?? {}).sort(), [data]);
  const loggerOptions = useMemo(() => {
    if (!data) return [];
    const names = service
      ? (data.catalog[service] ?? [])
      : Array.from(new Set(Object.values(data.catalog).flat()));
    return names.sort();
  }, [data, service]);

  function scopeLabel(setting: Pick<LogLevelSetting, "service" | "logger">): string {
    if (setting.logger) {
      const where =
        setting.service && setting.service !== "*"
          ? setting.service
          : t("sensors.logLevels.allServices");
      return `${where} › ${setting.logger}`;
    }
    return setting.service ?? t("sensors.logLevels.allServices");
  }

  async function run(action: () => Promise<LogLevels>, message: string) {
    if (!accessToken) return;
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      setData(await action());
      setStatus(message);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.actionError"));
    } finally {
      setBusy(false);
    }
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!accessToken) return;
    const input = {
      service: service || null,
      logger: logger.trim() || null,
      level,
      expires_in_minutes: duration || null,
    };
    void run(() => setLogLevel(accessToken, input), t("sensors.logLevels.saved"));
  }

  const now = Date.now();
  return (
    <section className={card} aria-labelledby="sensors-log-levels-heading">
      <h2 id="sensors-log-levels-heading">{t("sensors.logLevels.heading")}</h2>
      <p className="text-sm opacity-80">
        {t("sensors.logLevels.hint", {
          defaultMinutes: data?.debug_default_minutes ?? 60,
          maxHours: Math.round((data?.debug_max_minutes ?? 1440) / 60),
        })}
      </p>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
      {data && (
        <>
          <h3 className="mt-4 mb-2 text-base">{t("sensors.logLevels.current")}</h3>
          {data.settings.length === 0 ? (
            <p className="text-sm opacity-80">{t("sensors.logLevels.none")}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="text-left">
                    <th className="py-1 pr-3">{t("sensors.logLevels.scope")}</th>
                    <th className="py-1 pr-3">{t("sensors.logLevels.level")}</th>
                    <th className="py-1 pr-3">{t("sensors.logLevels.expires")}</th>
                    <th className="py-1 pr-3">{t("sensors.logLevels.changedBy")}</th>
                    {canManage && <th className="py-1" />}
                  </tr>
                </thead>
                <tbody>
                  {data.settings.map((setting) => {
                    const minutes = remainingMinutes(setting.expires_at, now);
                    return (
                      <tr key={setting.scope} className="border-t border-border">
                        <td className="py-1 pr-3 break-all">{scopeLabel(setting)}</td>
                        <td className="py-1 pr-3 font-semibold">{setting.level}</td>
                        <td className="py-1 pr-3">
                          {minutes === null ? (
                            t("sensors.logLevels.noExpiry")
                          ) : (
                            <span title={new Date(setting.expires_at!).toLocaleString(locale)}>
                              {t("sensors.logLevels.remaining", { minutes })}
                            </span>
                          )}
                        </td>
                        <td className="py-1 pr-3">
                          <PrincipalName value={setting.updated_by} />
                        </td>
                        {canManage && (
                          <td className="py-1 text-right">
                            <button
                              type="button"
                              className={plainButton}
                              disabled={busy}
                              aria-label={t("sensors.logLevels.removeLabel", {
                                scope: scopeLabel(setting),
                              })}
                              onClick={() =>
                                void run(
                                  () =>
                                    resetLogLevel(
                                      accessToken ?? "",
                                      setting.service === "*" ? null : setting.service,
                                      setting.logger
                                    ),
                                  t("sensors.logLevels.removed")
                                )
                              }
                            >
                              {t("sensors.logLevels.remove")}
                            </button>
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {canManage && (
            <form className="mt-4 flex flex-wrap items-end gap-3" onSubmit={submit}>
              <div className="flex flex-col gap-1 text-sm">
                <label htmlFor="log-level-service">{t("sensors.logLevels.service")}</label>
                <select
                  id="log-level-service"
                  className={fieldInput}
                  value={service}
                  disabled={busy}
                  onChange={(e) => setService(e.target.value)}
                >
                  <option value={ALL}>{t("sensors.logLevels.allServices")}</option>
                  {services.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex min-w-0 flex-col gap-1 text-sm">
                <label htmlFor="log-level-logger">{t("sensors.logLevels.logger")}</label>
                <input
                  id="log-level-logger"
                  list="log-level-logger-options"
                  className={`${fieldInput} w-72 max-w-full`}
                  value={logger}
                  disabled={busy}
                  placeholder={t("sensors.logLevels.loggerPlaceholder")}
                  onChange={(e) => setLogger(e.target.value)}
                />
                <datalist id="log-level-logger-options">
                  {loggerOptions.map((name) => (
                    <option key={name} value={name} />
                  ))}
                </datalist>
              </div>
              <div className="flex flex-col gap-1 text-sm">
                <label htmlFor="log-level-level">{t("sensors.logLevels.level")}</label>
                <select
                  id="log-level-level"
                  className={fieldInput}
                  value={level}
                  disabled={busy}
                  onChange={(e) => setLevel(e.target.value)}
                >
                  {LEVELS.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex flex-col gap-1 text-sm">
                <label htmlFor="log-level-duration">{t("sensors.logLevels.duration")}</label>
                <select
                  id="log-level-duration"
                  className={fieldInput}
                  value={duration}
                  disabled={busy}
                  onChange={(e) => setDuration(Number(e.target.value))}
                >
                  {DURATIONS.map((minutes) => (
                    <option
                      key={minutes}
                      value={minutes}
                      disabled={minutes === 0 && level === "DEBUG"}
                    >
                      {minutes === 0
                        ? t("sensors.logLevels.noExpiry")
                        : minutes < 60
                          ? t("sensors.logLevels.minutes", { minutes })
                          : t("sensors.logLevels.hours", { hours: minutes / 60 })}
                    </option>
                  ))}
                </select>
              </div>
              <button type="submit" className={primaryButton} disabled={busy}>
                {t("sensors.logLevels.set")}
              </button>
            </form>
          )}
          {status && (
            <p role="status" className="text-sm">
              {status}
            </p>
          )}

          <details className="mt-4">
            <summary className="cursor-pointer text-sm font-semibold">
              {t("sensors.logLevels.effective")}
            </summary>
            <div className="mt-2 overflow-x-auto">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="text-left">
                    <th className="py-1 pr-3">{t("sensors.logLevels.service")}</th>
                    <th className="py-1 pr-3">{t("sensors.logLevels.rootLevel")}</th>
                    <th className="py-1">{t("sensors.logLevels.loggerLevels")}</th>
                  </tr>
                </thead>
                <tbody>
                  {services.map((name) => {
                    const entry = data.effective[name];
                    return (
                      <tr key={name} className="border-t border-border">
                        <td className="py-1 pr-3">{name}</td>
                        <td className="py-1 pr-3">
                          {entry.root_level ?? t("sensors.logLevels.envLevel")}{" "}
                          <span className="opacity-70">
                            ({t(`sensors.logLevels.source.${entry.root_source}`)})
                          </span>
                        </td>
                        <td className="py-1 break-all">
                          {Object.entries(entry.loggers)
                            .map(([loggerName, value]) => `${loggerName} = ${value.level}`)
                            .join(", ") || "–"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </section>
  );
}
