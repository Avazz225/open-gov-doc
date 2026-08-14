"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";
import { MaintenanceBanner } from "./MaintenanceBanner";

// A static export has no server that could perform redirects before
// rendering (no middleware equivalent) - protection therefore kicks in
// client-side after the first render, once the auth state has loaded.
// Reading/opening process definitions remains open to every authenticated
// principal (workflow-service `GET /process-definitions*` is
// ungated) - only saving/deleting are additionally hidden behind
// `RequireCapability`, see there.
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
  return (
    <>
      <MaintenanceBanner />
      {children}
    </>
  );
}
