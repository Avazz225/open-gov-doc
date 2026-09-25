"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  type UserTrackingConfig,
  type UserTrackingRetentionConfig,
  type UserTrackingSession,
  getUserTrackingConfig,
  getUserTrackingRetentionConfig,
  listUserTrackingSessions,
  setUserTrackingConfig,
  setUserTrackingRetentionConfig,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Fine-grained user tracking (5.5, ADR 0157, Phase 52 Session 1) - has
// always been API-only until this session. Unlike every other admin page
// built so far, the backend splits this feature across two independent
// capabilities (`admin.user_tracking` for config, `admin.user_tracking_
// view` for session data) - the page itself (see page.tsx) is reachable
// with EITHER, and each section below does its own finer-grained check, so
// a view-only admin (e.g. an auditor) sees sessions but not the config
// form, and vice versa. No list-all endpoint exists for per-principal
// config (`GET /user-tracking-config/{id}` is a single-record lookup, no
// list) - unlike AdGroupMappings.tsx's "load everything, then CRUD rows"
// shape, this section is a lookup-by-id form instead.
export function UserTracking() {
  const { accessToken, user, permissions } = useAuth();
  const { t } = useI18n();
  const canManage = permissions.includes("admin.user_tracking");
  const canView = permissions.includes("admin.user_tracking_view");

  const [error, setError] = useState<string | null>(null);

  // Per-principal config lookup.
  const [lookupPrincipalId, setLookupPrincipalId] = useState("");
  const [config, setConfig] = useState<UserTrackingConfig | null>(null);
  const [configSaving, setConfigSaving] = useState(false);
  // Four-eyes principle (Post-Roadmap Phase 74 Session 2, ADR 0157's own
  // named gap) - same "don't reload, the toggle wasn't applied yet" idiom
  // as AdGroupMappings.tsx's own `mappingPending`.
  const [configPending, setConfigPending] = useState(false);

  // Sessions (optionally filtered by principal).
  const [sessionsFilter, setSessionsFilter] = useState("");
  const [sessions, setSessions] = useState<UserTrackingSession[]>([]);

  // Retention config (singleton).
  const [retention, setRetention] = useState<UserTrackingRetentionConfig | null>(null);
  const [retentionDaysInput, setRetentionDaysInput] = useState("");
  const [retentionSaving, setRetentionSaving] = useState(false);

  const reloadSessions = useCallback(
    async (principalId?: string) => {
      if (!accessToken || !canView) return;
      try {
        setSessions(await listUserTrackingSessions(accessToken, principalId || undefined));
      } catch (err) {
        setError(err instanceof ApiError ? err.message : t("userTracking.sessionsLoadError"));
      }
    },
    [accessToken, canView, t]
  );

  const reloadRetention = useCallback(async () => {
    if (!accessToken || !canManage) return;
    try {
      const r = await getUserTrackingRetentionConfig(accessToken);
      setRetention(r);
      setRetentionDaysInput(String(r.retention_days));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("userTracking.retentionLoadError"));
    }
  }, [accessToken, canManage, t]);

  useEffect(() => {
    reloadSessions();
  }, [reloadSessions]);

  useEffect(() => {
    reloadRetention();
  }, [reloadRetention]);

  async function handleLookupConfig(event: FormEvent) {
    event.preventDefault();
    if (!accessToken || !lookupPrincipalId.trim()) return;
    try {
      setConfig(await getUserTrackingConfig(accessToken, lookupPrincipalId.trim()));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("userTracking.configLoadError"));
    }
  }

  async function handleSaveConfig(enabled: boolean) {
    if (!accessToken || !config) return;
    setConfigSaving(true);
    setConfigPending(false);
    try {
      const result = await setUserTrackingConfig(accessToken, config.principal_id, {
        enabled,
        updatedBy: user?.sub ?? "",
      });
      if (result.status === "pending_approval") {
        // Four-eyes principle active - not yet applied, so the config
        // shown stays the previous (pre-toggle) value.
        setConfigPending(true);
      } else if (result.config) {
        setConfig(result.config);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("userTracking.configSaveError"));
    } finally {
      setConfigSaving(false);
    }
  }

  async function handleSessionsFilter(event: FormEvent) {
    event.preventDefault();
    await reloadSessions(sessionsFilter.trim());
  }

  async function handleSaveRetention(event: FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    const days = Number(retentionDaysInput);
    if (!Number.isInteger(days) || days < 1) {
      setError(t("userTracking.retentionInvalid"));
      return;
    }
    setRetentionSaving(true);
    try {
      setRetention(await setUserTrackingRetentionConfig(accessToken, days));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("userTracking.retentionSaveError"));
    } finally {
      setRetentionSaving(false);
    }
  }

  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50";
  const fieldInput =
    "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";
  const checkbox = "h-4 w-4 rounded border-border accent-accent";

  return (
    <>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      {canManage && (
        <section className="rounded-lg border border-border p-4 mb-6">
          <h2>{t("userTracking.configSectionTitle")}</h2>
          <p className="text-sm opacity-80">{t("userTracking.configHint")}</p>
          <form
            aria-label={t("userTracking.configFormLabel")}
            className="form-grid"
            onSubmit={handleLookupConfig}
          >
            <label>
              {t("userTracking.principalId")}
              <input
                value={lookupPrincipalId}
                onChange={(e) => setLookupPrincipalId(e.target.value)}
                required
                className={fieldInput}
              />
            </label>
            <button type="submit" className={secondaryBtn}>
              {t("userTracking.lookup")}
            </button>
          </form>

          {config && (
            <div className="form-grid">
              <p>
                <strong>{config.principal_id}</strong>
                {config.updated_by && (
                  <>
                    {" - "}
                    {t("userTracking.configLastChangedBy", {
                      username: config.updated_by,
                      date: new Date(config.updated_at).toLocaleString(),
                    })}
                  </>
                )}
              </p>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={config.enabled}
                  disabled={configSaving}
                  onChange={(e) => handleSaveConfig(e.target.checked)}
                  className={checkbox}
                />
                {t("userTracking.enabled")}
              </label>
              {configPending && (
                <p className="col-span-full text-sm opacity-80">
                  {t("userTracking.configPendingApproval")}
                </p>
              )}
            </div>
          )}
        </section>
      )}

      {canView && (
        <section className="rounded-lg border border-border p-4 mb-6">
          <h2>{t("userTracking.sessionsSectionTitle")}</h2>
          <p className="text-sm opacity-80">{t("userTracking.sessionsHint")}</p>
          <form
            aria-label={t("userTracking.sessionsFormLabel")}
            className="form-grid"
            onSubmit={handleSessionsFilter}
          >
            <label>
              {t("userTracking.principalId")}
              <input
                value={sessionsFilter}
                onChange={(e) => setSessionsFilter(e.target.value)}
                placeholder={t("userTracking.sessionsFilterPlaceholder")}
                className={fieldInput}
              />
            </label>
            <button type="submit" className={secondaryBtn}>
              {t("userTracking.filter")}
            </button>
          </form>

          <table className="data-table">
            <thead>
              <tr>
                <th>{t("userTracking.occurredAt")}</th>
                <th>{t("userTracking.principalId")}</th>
                <th>{t("userTracking.username")}</th>
                <th>{t("userTracking.eventType")}</th>
                <th>{t("userTracking.authMethod")}</th>
                <th>{t("userTracking.clientIp")}</th>
                <th>{t("userTracking.userAgent")}</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((s) => (
                <tr key={s.id}>
                  <td>{new Date(s.occurred_at).toLocaleString()}</td>
                  <td>{s.principal_id}</td>
                  <td>{s.username}</td>
                  <td>{s.event_type}</td>
                  <td>{s.auth_method}</td>
                  <td>{s.client_ip ?? "-"}</td>
                  <td>{s.user_agent ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {sessions.length === 0 && (
            <p className="italic opacity-70">{t("userTracking.sessionsEmpty")}</p>
          )}
        </section>
      )}

      {canManage && (
        <section className="rounded-lg border border-border p-4 mb-6">
          <h2>{t("userTracking.retentionSectionTitle")}</h2>
          <p className="text-sm opacity-80">{t("userTracking.retentionHint")}</p>
          <form
            aria-label={t("userTracking.retentionFormLabel")}
            className="form-grid"
            onSubmit={handleSaveRetention}
          >
            <label>
              {t("userTracking.retentionDays")}
              <input
                type="number"
                min={1}
                value={retentionDaysInput}
                onChange={(e) => setRetentionDaysInput(e.target.value)}
                required
                className={fieldInput}
              />
            </label>
            <button type="submit" disabled={retentionSaving} className={primaryBtn}>
              {t("common.save")}
            </button>
          </form>
          {retention && (
            <p className="text-sm opacity-80">
              {t("userTracking.retentionLastChanged", {
                date: new Date(retention.updated_at).toLocaleString(),
              })}
            </p>
          )}
        </section>
      )}
    </>
  );
}
