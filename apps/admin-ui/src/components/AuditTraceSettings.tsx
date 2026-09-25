"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  deleteAuditTraceRoleOverride,
  getAuditTraceConfig,
  listAuditTraceRoleOverrides,
  putAuditTraceRoleOverride,
  updateAuditTraceConfig,
  type AuditTraceConfig,
  type AuditTraceRoleOverride,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

type TriState = "default" | "on" | "off";

function triStateToValue(state: TriState): boolean | null {
  if (state === "default") return null;
  return state === "on";
}

function valueToTriState(value: boolean | null): TriState {
  if (value === null) return "default";
  return value ? "on" : "off";
}

// Audit-Tiefe fuer den Forensik-Trace (5.4b, seit P7-S2c) - Basis-
// Konfiguration (Default: alles protokollieren) + Rollen-Overrides, die pro
// Rolle mehr oder weniger protokollieren koennen. Konfliktregel bei
// mehreren Rollen desselben Nutzers: "protokollieren" gewinnt.
export function AuditTraceSettings() {
  const { accessToken } = useAuth();
  const { t } = useI18n();

  const [config, setConfig] = useState<AuditTraceConfig | null>(null);
  const [overrides, setOverrides] = useState<AuditTraceRoleOverride[]>([]);
  const [logViewed, setLogViewed] = useState(true);
  const [logDownloaded, setLogDownloaded] = useState(true);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [newRole, setNewRole] = useState("");
  const [newRoleViewed, setNewRoleViewed] = useState<TriState>("default");
  const [newRoleDownloaded, setNewRoleDownloaded] = useState<TriState>("default");

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setError(null);
    try {
      const [fetchedConfig, fetchedOverrides] = await Promise.all([
        getAuditTraceConfig(accessToken),
        listAuditTraceRoleOverrides(accessToken),
      ]);
      setConfig(fetchedConfig);
      setLogViewed(fetchedConfig.log_viewed);
      setLogDownloaded(fetchedConfig.log_downloaded);
      setOverrides(fetchedOverrides);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("auditTraceSettings.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [accessToken, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleSaveBase(event: FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    setIsSaving(true);
    setError(null);
    try {
      const updated = await updateAuditTraceConfig(accessToken, logViewed, logDownloaded);
      setConfig(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("auditTraceSettings.saveError"));
    } finally {
      setIsSaving(false);
    }
  }

  async function handleAddOverride(event: FormEvent) {
    event.preventDefault();
    if (!accessToken || !newRole.trim()) return;
    setError(null);
    try {
      await putAuditTraceRoleOverride(
        accessToken,
        newRole.trim(),
        triStateToValue(newRoleViewed),
        triStateToValue(newRoleDownloaded)
      );
      setNewRole("");
      setNewRoleViewed("default");
      setNewRoleDownloaded("default");
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("auditTraceSettings.saveError"));
    }
  }

  async function handleDeleteOverride(role: string) {
    if (!accessToken) return;
    setError(null);
    try {
      await deleteAuditTraceRoleOverride(accessToken, role);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("auditTraceSettings.saveError"));
    }
  }

  if (!accessToken) return null;

  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50";
  const fieldInput =
    "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";
  const checkbox = "h-4 w-4 rounded border-border accent-accent";

  return (
    <div>
      <p className="text-sm opacity-80">{t("auditTraceSettings.hint")}</p>

      <section className="rounded-lg border border-border p-4 mb-6">
        <h2>{t("auditTraceSettings.baseHeading")}</h2>
        {isLoading ? (
          <p>{t("common.loading")}</p>
        ) : (
          <form className="form-grid" onSubmit={handleSaveBase}>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={logViewed}
                onChange={(e) => setLogViewed(e.target.checked)}
                className={checkbox}
              />
              {t("auditTraceSettings.logViewed")}
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={logDownloaded}
                onChange={(e) => setLogDownloaded(e.target.checked)}
                className={checkbox}
              />
              {t("auditTraceSettings.logDownloaded")}
            </label>
            <div className="flex gap-2">
              <button type="submit" disabled={isSaving} className={primaryBtn}>
                {t("common.save")}
              </button>
            </div>
          </form>
        )}
        {config && (
          <p className="text-sm opacity-80">
            {t("auditTraceSettings.updatedAt")}: {new Date(config.updated_at).toLocaleString()}
          </p>
        )}
      </section>

      <section className="rounded-lg border border-border p-4 mb-6">
        <h2>{t("auditTraceSettings.overridesHeading")}</h2>
        <p className="text-sm opacity-80">{t("auditTraceSettings.overridesHint")}</p>
        <form
          className="form-grid"
          aria-label={t("auditTraceSettings.addOverrideLabel")}
          onSubmit={handleAddOverride}
        >
          <input
            placeholder={t("auditTraceSettings.rolePlaceholder")}
            value={newRole}
            onChange={(e) => setNewRole(e.target.value)}
            required
            className={fieldInput}
          />
          <select
            value={newRoleViewed}
            onChange={(e) => setNewRoleViewed(e.target.value as TriState)}
            aria-label={t("auditTraceSettings.overrideLogViewedLabel")}
            className={fieldInput}
          >
            <option value="default">{t("auditTraceSettings.triStateDefault")}</option>
            <option value="on">{t("auditTraceSettings.triStateOn")}</option>
            <option value="off">{t("auditTraceSettings.triStateOff")}</option>
          </select>
          <select
            value={newRoleDownloaded}
            onChange={(e) => setNewRoleDownloaded(e.target.value as TriState)}
            aria-label={t("auditTraceSettings.overrideLogDownloadedLabel")}
            className={fieldInput}
          >
            <option value="default">{t("auditTraceSettings.triStateDefault")}</option>
            <option value="on">{t("auditTraceSettings.triStateOn")}</option>
            <option value="off">{t("auditTraceSettings.triStateOff")}</option>
          </select>
          <button type="submit" className={primaryBtn}>
            {t("auditTraceSettings.addOverride")}
          </button>
        </form>

        {error && (
          <p className="text-danger" role="alert">
            {error}
          </p>
        )}

        {isLoading ? (
          <p>{t("common.loading")}</p>
        ) : overrides.length === 0 ? (
          <p className="italic opacity-70">{t("auditTraceSettings.overridesEmpty")}</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("auditTraceSettings.role")}</th>
                <th>{t("auditTraceSettings.logViewed")}</th>
                <th>{t("auditTraceSettings.logDownloaded")}</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {overrides.map((override) => (
                <tr key={override.role}>
                  <td>{override.role}</td>
                  <td>{t(`auditTraceSettings.triState${cap(valueToTriState(override.log_viewed))}`)}</td>
                  <td>
                    {t(`auditTraceSettings.triState${cap(valueToTriState(override.log_downloaded))}`)}
                  </td>
                  <td>
                    <button
                      type="button"
                      onClick={() => handleDeleteOverride(override.role)}
                      className={secondaryBtn}
                    >
                      {t("auditTraceSettings.deleteOverride")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function cap(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
