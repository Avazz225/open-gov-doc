"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  getLicenseComponents,
  getLicenseDisplayConfig,
  putLicenseDisplayConfig,
  type LicensableComponent,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { invalidateLicenseHints } from "@/lib/license-hints";

// "Mit Lizenzerweiterung verfügbar" (P91-S6): the licensable components and
// what each would add. The texts live here in the product (i18n
// `license.component.*`), not in the licence token. The switch turns the
// hints off installation-wide - this section stays visible to the licence
// admin either way, only its wording changes.
export function componentName(t: (key: string) => string, serviceType: string): string {
  const key = `license.component.${serviceType}.name`;
  const text = t(key);
  return text === key ? serviceType : text;
}

export function LicenseUpgradeHints({ reloadKey }: { reloadKey?: number }) {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [components, setComponents] = useState<LicensableComponent[] | null>(null);
  const [showHints, setShowHints] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!accessToken) return;
    Promise.all([getLicenseComponents(accessToken), getLicenseDisplayConfig(accessToken)])
      .then(([list, config]) => {
        setComponents(list);
        setShowHints(config.show_upgrade_hints);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : t("common.loadError")));
  }, [accessToken, reloadKey, t]);

  async function toggle(next: boolean) {
    if (!accessToken) return;
    setError(null);
    try {
      const saved = await putLicenseDisplayConfig(accessToken, next);
      setShowHints(saved.show_upgrade_hints);
      invalidateLicenseHints();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.saveError"));
    }
  }

  if (components === null) return error ? <p className="text-danger" role="alert">{error}</p> : null;
  // An installation without licensable components (e.g. the dev stack) has
  // nothing to extend - say so and leave out the switch.
  if (components.length === 0) {
    return (
      <section id="erweiterung" aria-labelledby="license-upgrade-heading" className="mb-4">
        <h2 id="license-upgrade-heading" className="text-base">
          {t("license.upgradeHeading")}
        </h2>
        <p className="text-sm">{t("license.noLicensableComponents")}</p>
      </section>
    );
  }
  const missing = components.filter((c) => c.status !== "licensed");

  return (
    <section id="erweiterung" aria-labelledby="license-upgrade-heading" className="mb-4">
      <h2 id="license-upgrade-heading" className="text-base">
        {t("license.upgradeHeading")}
      </h2>
      {missing.length === 0 ? (
        <p className="text-sm">{t("license.allLicensed")}</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-3 p-0">
          {missing.map((c) => (
            <li key={c.service_type} className="rounded-md border border-border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <strong>{componentName(t, c.service_type)}</strong>
                <span className={`badge ${c.status === "unlicensed" ? "down" : "warn"}`}>
                  {t(`license.componentStatus.${c.status}`)}
                </span>
              </div>
              <p className="mb-0 mt-1 text-sm">{t(`license.component.${c.service_type}.benefit`)}</p>
            </li>
          ))}
        </ul>
      )}
      <label className="checkbox-label mt-3 text-sm">
        <input
          type="checkbox"
          checked={showHints}
          onChange={(e) => toggle(e.target.checked)}
          className="h-4 w-4 rounded border-border accent-accent"
        />
        {t("license.showHints")}
      </label>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
