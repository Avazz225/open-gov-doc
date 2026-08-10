"use client";

import { useState } from "react";
import { useI18n } from "@/i18n";
import { ThemeSwitcher } from "./ThemeSwitcher";

export type WorkspaceView =
  | "documents"
  | "search"
  | "approvals"
  | "favorites"
  | "teamspaces"
  | "delegations"
  | "trash";

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
