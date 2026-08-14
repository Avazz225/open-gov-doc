"use client";

import type { ReactNode } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";
import { AdminSidebar } from "./AdminSidebar";
import { InstallationSwitcher } from "./InstallationSwitcher";
import { LicenseStatusBanner } from "./LicenseStatusBanner";
import { MaintenanceBanner } from "./MaintenanceBanner";
import { ThemeSwitcher } from "./ThemeSwitcher";

// Management dashboard layout (P4-S5, user feedback after P4-S3, concept 8):
// replaces the previous flat top-nav bar with a left-hand navigation
// sidebar (`AdminSidebar`); the main area on the right shows the currently
// selected function. The `InstallationSwitcher` sits in the header bar
// because - unlike the side navigation - it is installation-wide rather
// than scoped to a specific section.
export function AdminShell({ title, children }: { title: string; children: ReactNode }) {
  const { user, logout } = useAuth();
  const { t } = useI18n();

  return (
    <div className="admin-shell">
      <MaintenanceBanner />
      <LicenseStatusBanner />
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
