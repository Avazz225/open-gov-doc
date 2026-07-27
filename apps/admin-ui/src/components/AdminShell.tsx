"use client";

import type { ReactNode } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";
import { AdminSidebar } from "./AdminSidebar";
import { InstallationSwitcher } from "./InstallationSwitcher";
import { ThemeSwitcher } from "./ThemeSwitcher";

// Management-Dashboard-Layout (P4-S5, Nutzer-Feedback nach P4-S3, Konzept 8):
// ersetzt die vorherige flache Top-Nav-Leiste durch eine linke
// Navigationsseitenleiste (`AdminSidebar`), Hauptbereich rechts zeigt die
// jeweils gewählte Funktion. Der `InstallationSwitcher` sitzt in der
// Kopfzeile, da er - anders als die Seitennavigation - installationsweit
// und nicht bereichsspezifisch ist.
export function AdminShell({ title, children }: { title: string; children: ReactNode }) {
  const { user, logout } = useAuth();
  const { t } = useI18n();

  return (
    <div className="admin-shell">
      <div className="top-bar">
        <h1>{title}</h1>
        <div className="top-bar-actions">
          <InstallationSwitcher />
          <ThemeSwitcher />
          {user && <span>{user.username} </span>}
          <button type="button" onClick={logout}>
            {t("common.logout")}
          </button>
        </div>
      </div>
      <div className="admin-body">
        <AdminSidebar />
        <main className="admin-content">{children}</main>
      </div>
    </div>
  );
}
