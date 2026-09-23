"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";

// Defense in depth for domain-separated admin roles (4.6, P6-S5):
// `AdminSidebar` already hides entries without the required capability, but
// a directly navigated link still needs to be protected independently of
// the server (static export, no middleware equivalent, see RequireAuth).
// Assumes the page is already nested inside `<RequireAuth>` - `permissions`
// is only meaningful after a successful login/session restore.
export function RequireCapability({
  capability,
  children,
}: {
  // Phase 52 Session 1 (user tracking, ADR 0157): the first page whose
  // backend endpoints are split across two independent capabilities
  // (`admin.user_tracking`/`admin.user_tracking_view`) with no single
  // capability covering the whole page - an array checks "has ANY of
  // these", so the page itself still renders (each section then does its
  // own finer-grained `permissions.includes(...)` check, same as this
  // component). Every existing caller passes a single string, unaffected.
  capability: string | string[];
  children: ReactNode;
}) {
  const { permissions, isLoading } = useAuth();
  const router = useRouter();
  const { t } = useI18n();
  const required = Array.isArray(capability) ? capability : [capability];
  const allowed = required.some((c) => permissions.includes(c));

  useEffect(() => {
    if (!isLoading && !allowed) {
      router.replace("/");
    }
  }, [isLoading, allowed, router]);

  if (isLoading) {
    return <p className="mx-auto max-w-[1100px] p-6">{t("common.loading")}</p>;
  }
  if (!allowed) {
    return null;
  }
  return <>{children}</>;
}
