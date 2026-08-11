"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/i18n";
import { ThemeSwitcher } from "./ThemeSwitcher";

export type WorkspaceView =
  | "documents"
  | "search"
  | "approvals"
  | "favorites"
  | "teamspaces"
  | "delegations"
  | "trash"
  | "quarantine"
  | "poststelle"
  | "kontakte";

// Quarantäne-Bereich (2.5/10.3, P15-S2) - anders als der Papierkorb (immer
// zumindest in der persönlichen Sicht sichtbar) gibt es hier laut Konzept
// KEINE allgemein zugängliche Sicht: "eine eigene, eng begrenzte Rolle darf
// einen Quarantäne-Fall einsehen" - der Icon-Rail-Eintrag selbst bleibt für
// alle anderen Rollen unsichtbar, nicht nur die Aktionen darin (gleiches
// unabhängig konfigurierbares Rollen-Setting-Muster wie überall im Projekt).
const QUARANTINE_ADMIN_ROLE = "dms-admin";

// Posteingang/Postausgang (2.5/3.3, P15-S3) - gleiches Muster: "Nur eine
// dedizierte Poststelle-Rolle sieht/bearbeitet den ungesichteten Zulauf."
const POSTSTELLE_ROLE = "dms-poststelle";

// Ganz linker Rand, außerhalb des dreigeteilten Main-Contents (Nutzer-
// Feedback nach P4-S3, 8): iconbasierte Cross-Cutting-Navigation. "Dokumente"
// und seit P5-S4 auch "Suche" schalten zwischen den beiden Ansichten in
// DocumentWorkspace um. "Einstellungen" öffnet seit P4-S6 ein Popover mit
// dem Theme-Umschalter statt weiter deaktiviert zu sein.
export function IconRail({
  activeView,
  onSelectView,
}: {
  activeView: WorkspaceView;
  onSelectView: (view: WorkspaceView) => void;
}) {
  const { t } = useI18n();
  const { user } = useAuth();
  const isQuarantineAdmin = Boolean(user?.realm_roles.includes(QUARANTINE_ADMIN_ROLE));
  const isPoststelle = Boolean(user?.realm_roles.includes(POSTSTELLE_ROLE));
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  return (
    <nav className="icon-rail" aria-label={t("iconRail.label")}>
      <button
        type="button"
        className={`icon-rail-button${activeView === "documents" ? " icon-rail-active" : ""}`}
        title={t("iconRail.documents")}
        aria-current={activeView === "documents" ? "page" : undefined}
        onClick={() => onSelectView("documents")}
      >
        <span aria-hidden="true">📄</span>
      </button>
      <button
        type="button"
        className={`icon-rail-button${activeView === "search" ? " icon-rail-active" : ""}`}
        title={t("iconRail.search")}
        aria-current={activeView === "search" ? "page" : undefined}
        onClick={() => onSelectView("search")}
      >
        <span aria-hidden="true">🔍</span>
      </button>
      <button
        type="button"
        className={`icon-rail-button${activeView === "approvals" ? " icon-rail-active" : ""}`}
        title={t("iconRail.approvals")}
        aria-current={activeView === "approvals" ? "page" : undefined}
        onClick={() => onSelectView("approvals")}
      >
        <span aria-hidden="true">✅</span>
      </button>
      <button
        type="button"
        className={`icon-rail-button${activeView === "favorites" ? " icon-rail-active" : ""}`}
        title={t("iconRail.favorites")}
        aria-current={activeView === "favorites" ? "page" : undefined}
        onClick={() => onSelectView("favorites")}
      >
        <span aria-hidden="true">⭐</span>
      </button>
      <button
        type="button"
        className={`icon-rail-button${activeView === "teamspaces" ? " icon-rail-active" : ""}`}
        title={t("iconRail.teamspaces")}
        aria-current={activeView === "teamspaces" ? "page" : undefined}
        onClick={() => onSelectView("teamspaces")}
      >
        <span aria-hidden="true">👥</span>
      </button>
      <button
        type="button"
        className={`icon-rail-button${activeView === "delegations" ? " icon-rail-active" : ""}`}
        title={t("iconRail.delegations")}
        aria-current={activeView === "delegations" ? "page" : undefined}
        onClick={() => onSelectView("delegations")}
      >
        <span aria-hidden="true">🧑‍🤝‍🧑</span>
      </button>
      <button
        type="button"
        className={`icon-rail-button${activeView === "trash" ? " icon-rail-active" : ""}`}
        title={t("iconRail.trash")}
        aria-current={activeView === "trash" ? "page" : undefined}
        onClick={() => onSelectView("trash")}
      >
        <span aria-hidden="true">🗑️</span>
      </button>
      {isQuarantineAdmin && (
        <button
          type="button"
          className={`icon-rail-button${activeView === "quarantine" ? " icon-rail-active" : ""}`}
          title={t("iconRail.quarantine")}
          aria-current={activeView === "quarantine" ? "page" : undefined}
          onClick={() => onSelectView("quarantine")}
        >
          <span aria-hidden="true">☣️</span>
        </button>
      )}
      {isPoststelle && (
        <button
          type="button"
          className={`icon-rail-button${activeView === "poststelle" ? " icon-rail-active" : ""}`}
          title={t("iconRail.poststelle")}
          aria-current={activeView === "poststelle" ? "page" : undefined}
          onClick={() => onSelectView("poststelle")}
        >
          <span aria-hidden="true">📬</span>
        </button>
      )}
      <button
        type="button"
        className={`icon-rail-button${activeView === "kontakte" ? " icon-rail-active" : ""}`}
        title={t("iconRail.kontakte")}
        aria-current={activeView === "kontakte" ? "page" : undefined}
        onClick={() => onSelectView("kontakte")}
      >
        <span aria-hidden="true">📇</span>
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
