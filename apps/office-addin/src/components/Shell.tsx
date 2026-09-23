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
    <div className="p-3">
      <div className="mb-3 flex items-center justify-between border-b border-border pb-2 text-sm">
        <span className="font-semibold">{t("meta.title")}</span>
        <div className="flex items-center gap-2">
          {user && <span>{user.username}</span>}
          <button
            type="button"
            onClick={logout}
            className="rounded-md border border-border bg-hover-bg px-3 py-1 text-fg transition-colors hover:bg-accent-bg"
          >
            {t("common.logout")}
          </button>
        </div>
      </div>
      <main>{children}</main>
    </div>
  );
}
