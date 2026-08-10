"use client";

import type { ReactNode } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";

// Bewusst kein Tab-Nav/Theme-Umschalter/Wartungsbanner wie bei den übrigen
// Apps - der Taskpane ist eine schmale Seitenleiste (~300-450px) mit genau
// EINER Ansicht, kein Platz und kein Bedarf für zusätzliche Chrome. Ein
// Add-in sollte sich idealerweise am Office-eigenen Theme orientieren
// (`Office.context.officeTheme`) statt einem eigenen Umschalter - nicht Teil
// dieser Session, siehe docs/services/office-addin.md "Offene Punkte".
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
