"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";
import { LocaleSwitcher } from "./LocaleSwitcher";
import { MaintenanceBanner } from "./MaintenanceBanner";
import { ThemeSwitcher } from "./ThemeSwitcher";

// Lean shell (concept 8: "lean focus on approval tasks only") - originally
// two areas, a third ("Team") added in post-roadmap phase 31 session 11 for
// the supervisor/team task oversight view, a fourth ("Vorgänge"/"Cases")
// added in Post-Roadmap Phase 74 Session 1 (ADR 0141's own named gap: a
// reviewer working a case-bound task previously had no case-context view)
// - still a simple tab bar instead of full page navigation like AdminShell
// (admin-ui). Same header pattern (title/theme/logout) as the other apps.
export function Shell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const { t } = useI18n();
  const pathname = usePathname();

  const tabClass = (active: boolean) =>
    active
      ? "border-b-2 border-accent pb-1 font-semibold text-fg no-underline"
      : "pb-1 text-fg no-underline opacity-70";

  return (
    <div className="mx-auto max-w-[1100px] p-6">
      <MaintenanceBanner />
      <div className="mb-6 flex items-center justify-between border-b border-border pb-3">
        <nav className="flex gap-6">
          <Link href="/" className={tabClass(pathname === "/")}>
            {t("nav.tasks")}
          </Link>
          <Link href="/approvals/" className={tabClass(Boolean(pathname?.startsWith("/approvals")))}>
            {t("nav.approvals")}
          </Link>
          <Link href="/team/" className={tabClass(Boolean(pathname?.startsWith("/team")))}>
            {t("nav.team")}
          </Link>
          <Link href="/cases/" className={tabClass(Boolean(pathname?.startsWith("/cases")))}>
            {t("nav.cases")}
          </Link>
        </nav>
        <div className="flex items-center gap-4">
          <LocaleSwitcher />
          <ThemeSwitcher />
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
