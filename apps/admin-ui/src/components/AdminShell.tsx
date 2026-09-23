"use client";

import type { ReactNode } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";
import { AdminSidebar } from "./AdminSidebar";
import { InstallationSwitcher } from "./InstallationSwitcher";
import { LicenseStatusBanner } from "./LicenseStatusBanner";
import { LocaleSwitcher } from "./LocaleSwitcher";
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
    <div className="flex min-h-screen flex-col">
      <MaintenanceBanner />
      <LicenseStatusBanner />
      <div className="flex items-center justify-between border-b border-border px-6 py-3">
        <h1>{title}</h1>
        <div className="flex items-center gap-4">
          <InstallationSwitcher />
          <LocaleSwitcher />
          <ThemeSwitcher />
          {user && <span>{user.username} </span>}
          <button
            type="button"
            onClick={logout}
            className="rounded-md border border-border bg-hover-bg px-3 py-1 text-fg transition-colors hover:bg-accent-bg"
          >
            {t("common.logout")}
          </button>
        </div>
      </div>
      <div className="flex min-h-0 flex-1">
        <AdminSidebar />
        <main className="max-w-[1100px] flex-1 min-w-0 p-6">{children}</main>
      </div>
    </div>
  );
}
