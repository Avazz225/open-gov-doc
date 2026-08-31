"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";
import { MaintenanceBanner } from "./MaintenanceBanner";
import { ThemeSwitcher } from "./ThemeSwitcher";

// Lean shell (concept 8: "lean focus on approval tasks only") - originally
// two areas, a third ("Team") added in post-roadmap phase 31 session 11 for
// the supervisor/team task oversight view - still a simple tab bar instead
// of full page navigation like AdminShell (admin-ui). Same header pattern
// (title/theme/logout) as the other apps.
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
          <Link
            href="/team/"
            className={pathname?.startsWith("/team") ? "tab-active" : undefined}
          >
            {t("nav.team")}
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
