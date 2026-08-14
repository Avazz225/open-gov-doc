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
  | "kontakte"
  | "aussonderung"
  | "vorlagen";

// Quarantine area (2.5/10.3, P15-S2) - unlike the trash (always visible at
// least in the personal view), the concept specifies NO generally
// accessible view here: "a dedicated, narrowly scoped role may view a
// quarantine case" - the icon rail entry itself stays invisible to all
// other roles, not just the actions within it (same independently
// configurable role-setting pattern used throughout the project).
const QUARANTINE_ADMIN_ROLE = "dms-admin";

// Inbox/outbox (2.5/3.3, P15-S3) - same pattern: "Only a dedicated mail
// room role sees/processes the unscreened incoming items."
const POSTSTELLE_ROLE = "dms-poststelle";

// Records disposal access area (2.5/5.6, P15-S5) - same role gate as
// archival-services `archive_retrieval_role` (concept 2.5: "a dedicated
// archive/registry role"), unlike the ungated view of the contacts area.
const ARCHIVAL_ACCESS_ROLE = "dms-admin";

// Far left edge, outside the three-way split main content (user feedback
// after P4-S3, 8): icon-based cross-cutting navigation. "Dokumente"
// and, since P5-S4, also "Suche" toggle between the two views in
// DocumentWorkspace. Since P4-S6, "Einstellungen" opens a popover with the
// theme switcher instead of remaining disabled.
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
  const isArchivalAccess = Boolean(user?.realm_roles.includes(ARCHIVAL_ACCESS_ROLE));
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
      {isArchivalAccess && (
        <button
          type="button"
          className={`icon-rail-button${activeView === "aussonderung" ? " icon-rail-active" : ""}`}
          title={t("iconRail.aussonderung")}
          aria-current={activeView === "aussonderung" ? "page" : undefined}
          onClick={() => onSelectView("aussonderung")}
        >
          <span aria-hidden="true">🗄️</span>
        </button>
      )}
      <button
        type="button"
        className={`icon-rail-button${activeView === "vorlagen" ? " icon-rail-active" : ""}`}
        title={t("iconRail.vorlagen")}
        aria-current={activeView === "vorlagen" ? "page" : undefined}
        onClick={() => onSelectView("vorlagen")}
      >
        <span aria-hidden="true">📐</span>
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
