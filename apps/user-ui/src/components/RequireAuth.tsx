"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";
import { MaintenanceBanner } from "./MaintenanceBanner";

// A static export has no server that could run redirects before
// rendering (no middleware equivalent) - the protection therefore kicks
// in client-side after the first render, once the auth state has loaded.
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth();
  const router = useRouter();
  const { t } = useI18n();

  useEffect(() => {
    if (!isLoading && !user) {
      // Direct links (post-roadmap feature, Phase 27, ADR 0106): preserve the
      // page the user was trying to reach so login/page.tsx can send them
      // back there instead of always landing on "/" - relevant once
      // ?document=/?folder= deep links exist (Phase 29).
      const target = `${window.location.pathname}${window.location.search}`;
      router.replace(`/login/?returnTo=${encodeURIComponent(target)}`);
    }
  }, [isLoading, user, router]);

  if (isLoading) {
    return <p className="page">{t("common.loading")}</p>;
  }
  if (!user) {
    return null;
  }
  return (
    <>
      <MaintenanceBanner />
      {children}
    </>
  );
}
