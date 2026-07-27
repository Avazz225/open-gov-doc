"use client";

import { useI18n } from "@/i18n";
import { useInstallation } from "@/lib/installation-context";

// Kern der Multi-Installation-Anforderung (P4-S5, Konzept 3a/8): Wechsel der
// aktiven Installation ohne erneute Anmeldung, solange deren Sitzung noch
// gültig ist (siehe `auth-context.tsx`). Bewusst ausgeblendet, solange nur
// eine Installation konfiguriert ist - nichts zum Wechseln, unnötiger UI-Ballast.
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
