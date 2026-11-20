"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";
import { LocaleSwitcher } from "./LocaleSwitcher";
import { MaintenanceBanner } from "./MaintenanceBanner";
import { ThemeSwitcher } from "./ThemeSwitcher";

// A static export has no server that could perform redirects before
// rendering (no middleware equivalent) - protection therefore kicks in
// client-side after the first render, once the auth state has loaded.
// Reading/opening process definitions remains open to every authenticated
// principal (workflow-service `GET /process-definitions*` is
// ungated) - only saving/deleting are additionally hidden behind
// `RequireCapability`, see there.
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, isLoading, logout } = useAuth();
  const router = useRouter();
  const { t } = useI18n();

  useEffect(() => {
    if (!isLoading && !user) {
      // Direct links (post-roadmap feature, Phase 27, ADR 0106): preserve
      // the page the user was trying to reach so login/page.tsx can send
      // them back there instead of always landing on "/" - relevant once
      // ?document=/?folder= deep links exist (Phase 29).
      const target = `${window.location.pathname}${window.location.search}`;
      router.replace(`/login/?returnTo=${encodeURIComponent(target)}`);
    }
  }, [isLoading, user, router]);

  if (isLoading) {
    return <p className="mx-auto max-w-[1100px] p-6">{t("common.loading")}</p>;
  }
  if (!user) {
    return null;
  }
  return (
    <>
      <MaintenanceBanner />
      {/* P83-S7 (punch list item 10): the same header cluster as reviewer-ui/
          migration-console - language, theme, the signed-in user and log out,
          right-aligned over a rule. It wraps as a group on narrow screens. */}
      <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-2 border-b border-border px-6 py-3">
        <LocaleSwitcher />
        <ThemeSwitcher />
        {user && <span className="whitespace-nowrap">{user.username}</span>}
        <button
          type="button"
          onClick={logout}
          className="whitespace-nowrap rounded-md border border-border bg-hover-bg px-3 py-1 text-fg transition-colors hover:bg-accent-bg"
        >
          {t("common.logout")}
        </button>
      </div>
      {children}
    </>
  );
}
