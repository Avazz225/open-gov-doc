"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";
import { MaintenanceBanner } from "./MaintenanceBanner";
import { ThemeSwitcher } from "./ThemeSwitcher";

// Lean shell (concept 8: "lean focus on approval tasks only") - only two
// areas, hence a simple tab bar instead of full page navigation like
// AdminShell (admin-ui). Same header pattern (title/theme/logout) as the
// other apps.
export function Shell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const { t } = useI18n();
  const pathname = usePathname();

  return (
    <div className="page">
      <MaintenanceBanner />
      <div className="top-bar">
        <nav className="tab-nav">
          <Link href="/" className={pathname === "/" ? "tab-active" : undefined}>
            {t("nav.tasks")}
          </Link>
          <Link
            href="/approvals/"
            className={pathname?.startsWith("/approvals") ? "tab-active" : undefined}
          >
            {t("nav.approvals")}
          </Link>
        </nav>
        <div className="top-bar-actions">
          <ThemeSwitcher />
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
