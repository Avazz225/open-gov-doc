"use client";

import type { ReactNode } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";

// Deliberately no tab nav/theme switcher/maintenance banner like the other
// apps - the task pane is a narrow sidebar (~300-450px) with exactly ONE
// view, no room and no need for additional chrome. An add-in should
// ideally follow Office's own theme (`Office.context.officeTheme`) instead
// of its own switcher - not part of this session, see
// docs/services/office-addin.md "Open Points".
export function Shell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const { t } = useI18n();

  return (
    <div className="page">
      <div className="top-bar">
        <span className="app-title">{t("meta.title")}</span>
        <div className="top-bar-actions">
          {user && <span>{user.username}</span>}
          <button type="button" onClick={logout}>
            {t("common.logout")}
          </button>
        </div>
      </div>
      <main>{children}</main>
    </div>
  );
}
