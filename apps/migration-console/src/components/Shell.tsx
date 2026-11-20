"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useI18n } from "@/i18n";
import { AccountMenu } from "./AccountMenu";
import { MaintenanceBanner } from "./MaintenanceBanner";

// Lightweight console for transfer operations (concept 8) - only two areas,
// so a simple tab bar instead of full page navigation like AdminShell
// (admin-ui). Same header pattern (title/theme/logout) as the other apps.
// Narrow widths (P83-S10, ADR 0238 Decision 2): the tabs scroll inside their
// own strip instead of pushing the page, and the header cluster collapses
// into AccountMenu's button below 768px.
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
            {t("nav.transfers")}
          </Link>
          <Link
            href="/paired-installations/"
            className={tabClass(Boolean(pathname?.startsWith("/paired-installations")))}
          >
            {t("nav.pairedInstallations")}
          </Link>
        </nav>
        <AccountMenu />
      </div>
      <main>{children}</main>
    </div>
  );
}
