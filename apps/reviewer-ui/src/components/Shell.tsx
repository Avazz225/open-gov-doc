"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useI18n } from "@/i18n";
import { AccountMenu } from "./AccountMenu";
import { MaintenanceBanner } from "./MaintenanceBanner";

// Lean shell (concept 8: "lean focus on approval tasks only") - originally
// two areas, a third ("Team") added in post-roadmap phase 31 session 11 for
// the supervisor/team task oversight view, a fourth ("Vorgänge"/"Cases")
// added in Post-Roadmap Phase 74 Session 1 (ADR 0141's own named gap: a
// reviewer working a case-bound task previously had no case-context view)
// - still a simple tab bar instead of full page navigation like AdminShell
// (admin-ui). Same header pattern (title/theme/logout) as the other apps.
// Narrow widths (P83-S11, ADR 0238 Decision 2): the four tabs scroll inside
// their own strip, and the header cluster collapses into AccountMenu's
// button below 1024px - it shared the tab row and overflowed even at 768px.
export function Shell({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const pathname = usePathname();

  const tabClass = (active: boolean) =>
    active
      ? "border-b-2 border-accent pb-1 font-semibold text-fg no-underline"
      : "pb-1 text-fg no-underline opacity-70";

  return (
    <div className="mx-auto max-w-[1100px] p-4 md:p-6">
      <MaintenanceBanner />
      <div className="mb-6 flex items-center justify-between gap-4 border-b border-border pb-3">
        <nav className="dms-scroll-x dms-tab-strip min-w-0 gap-6">
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
        <AccountMenu inlineFrom="lg" />
      </div>
      <main>{children}</main>
    </div>
  );
}
