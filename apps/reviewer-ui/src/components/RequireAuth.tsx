"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";
import { Shell } from "./Shell";

// A static export has no server that could perform redirects before
// rendering (no middleware equivalent) - the guard therefore kicks in
// client-side after the first render, once the auth state has loaded.
// Neither task completion nor approval decisions are capability-gated on the
// backend (see docs/services/reviewer-ui.md "Authorization") - this
// component only checks whether a valid session exists at all.
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth();
  const router = useRouter();
  const { t } = useI18n();

  useEffect(() => {
    if (!isLoading && !user) {
      router.replace("/login/");
    }
  }, [isLoading, user, router]);

  if (isLoading) {
    return <p className="page">{t("common.loading")}</p>;
  }
  if (!user) {
    return null;
  }
  return <Shell>{children}</Shell>;
}
