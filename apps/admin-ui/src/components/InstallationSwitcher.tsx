"use client";

import { useI18n } from "@/i18n";
import { useInstallation } from "@/lib/installation-context";

// Core of the multi-installation requirement (P4-S5, concept 3a/8):
// switching the active installation without logging in again, as long as
// its session is still valid (see `auth-context.tsx`). Deliberately hidden
// as long as only one installation is configured - nothing to switch to,
// unnecessary UI clutter.
export function InstallationSwitcher() {
  const { t } = useI18n();
  const { installations, activeInstallation, switchInstallation } = useInstallation();

  if (installations.length <= 1) return null;

  return (
    <label className="installation-switcher">
      {t("installations.switcherLabel")}
      <select
        value={activeInstallation.id}
        onChange={(event) => switchInstallation(event.target.value)}
      >
        {installations.map((installation) => (
          <option key={installation.id} value={installation.id}>
            {installation.name}
          </option>
        ))}
      </select>
    </label>
  );
}
