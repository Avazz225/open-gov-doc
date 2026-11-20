"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { Menu } from "lucide-react";
import { useI18n } from "@/i18n";
import { AccountMenu } from "./AccountMenu";
import { AdminSidebar } from "./AdminSidebar";
import { InstallationSwitcher } from "./InstallationSwitcher";
import { LicenseStatusBanner } from "./LicenseStatusBanner";
import { MaintenanceBanner } from "./MaintenanceBanner";
import { NavDrawer } from "./NavDrawer";

// Management dashboard layout (P4-S5, user feedback after P4-S3, concept 8):
// replaces the previous flat top-nav bar with a left-hand navigation
// sidebar (`AdminSidebar`); the main area on the right shows the currently
// selected function. The `InstallationSwitcher` sits in the header bar
// because - unlike the side navigation - it is installation-wide rather
// than scoped to a specific section.
//
// Narrow widths (P83-S12, ADR 0238 Decisions 2 and 3): below 768px the
// sidebar becomes a modal drawer behind a menu button; the header cluster
// (installation, language, theme, user, log out) collapses into the account
// menu below 1024px, where title plus cluster no longer fit one row.
export function AdminShell({ title, children }: { title: string; children: ReactNode }) {
  const { t } = useI18n();
  const pathname = usePathname();
  const [navOpen, setNavOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);

  // Navigating from the drawer closes it.
  useEffect(() => {
    setNavOpen(false);
  }, [pathname]);

  return (
    <div className="flex min-h-screen flex-col">
      <MaintenanceBanner />
      <LicenseStatusBanner />
      <div className="flex items-center gap-3 border-b border-border px-4 py-2 md:gap-4 md:px-6 md:py-3">
        <button
          ref={menuButtonRef}
          type="button"
          onClick={() => setNavOpen(true)}
          aria-label={t("nav.openMenu")}
          aria-expanded={navOpen}
          className="flex min-h-10 min-w-10 flex-none items-center justify-center rounded-md border border-transparent bg-transparent text-fg transition-colors hover:bg-hover-bg md:hidden"
        >
          <Menu size={20} strokeWidth={1.75} aria-hidden="true" />
        </button>
        <h1 className="min-w-0 flex-1 truncate max-md:text-xl">{title}</h1>
        <AccountMenu inlineFrom="lg" leading={(touch) => <InstallationSwitcher touch={touch} />} />
      </div>
      <div className="flex min-h-0 flex-1">
        <div className="hidden md:flex">
          <AdminSidebar />
        </div>
        <main className="max-w-[1100px] flex-1 min-w-0 p-4 md:p-6">{children}</main>
      </div>
      <NavDrawer
        open={navOpen}
        onClose={() => setNavOpen(false)}
        label={t("nav.ariaLabel")}
        closeLabel={t("nav.closeMenu")}
        returnFocusRef={menuButtonRef}
      >
        <AdminSidebar variant="drawer" />
      </NavDrawer>
    </div>
  );
}
