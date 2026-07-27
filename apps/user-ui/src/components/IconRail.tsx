"use client";

import { useState } from "react";
import { useI18n } from "@/i18n";
import { ThemeSwitcher } from "./ThemeSwitcher";

// Ganz linker Rand, außerhalb des dreigeteilten Main-Contents (Nutzer-
// Feedback nach P4-S3, 8): iconbasierte Cross-Cutting-Navigation. "Dokumente"
// ist funktional, "Suche" bleibt ein sichtbarer, deaktivierter Platzhalter
// (folgt erst mit P5-S4) statt wegzulassen und die Navigationsstruktur
// später erneut umzubauen. "Einstellungen" öffnet seit P4-S6 ein Popover mit
// dem Theme-Umschalter statt weiter deaktiviert zu sein.
export function IconRail() {
  const { t } = useI18n();
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  return (
    <nav className="icon-rail" aria-label={t("iconRail.label")}>
      <button
        type="button"
        className="icon-rail-button icon-rail-active"
        title={t("iconRail.documents")}
        aria-current="page"
      >
        <span aria-hidden="true">📄</span>
      </button>
      <button
        type="button"
        className="icon-rail-button"
        disabled
        title={t("iconRail.searchComingSoon")}
      >
        <span aria-hidden="true">🔍</span>
      </button>
      <div className="icon-rail-settings">
        <button
          type="button"
          className={`icon-rail-button${isSettingsOpen ? " icon-rail-active" : ""}`}
          title={t("iconRail.settings")}
          aria-haspopup="true"
          aria-expanded={isSettingsOpen}
          onClick={() => setIsSettingsOpen((prev) => !prev)}
        >
          <span aria-hidden="true">⚙️</span>
        </button>
        {isSettingsOpen && (
          <div className="icon-rail-popover" aria-label={t("iconRail.settings")}>
            <ThemeSwitcher />
          </div>
        )}
      </div>
    </nav>
  );
}
