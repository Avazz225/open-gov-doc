"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";
import { MaintenanceBanner } from "./MaintenanceBanner";
import { ThemeSwitcher } from "./ThemeSwitcher";

// Schlanke Konsole für Transfer-Vorgänge (Konzept 8) - nur zwei Bereiche,
// daher eine einfache Tab-Leiste statt einer vollen Seitennavigation wie
// AdminShell (admin-ui). Gleiches Kopfzeilen-Muster (Titel/Theme/Logout) wie
// die übrigen Apps.
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
            {t("nav.transfers")}
          </Link>
          <Link
            href="/paired-installations/"
            className={pathname?.startsWith("/paired-installations") ? "tab-active" : undefined}
          >
            {t("nav.pairedInstallations")}
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
