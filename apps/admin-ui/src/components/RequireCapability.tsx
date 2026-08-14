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
  capability: string;
  children: ReactNode;
}) {
  const { permissions, isLoading } = useAuth();
  const router = useRouter();
  const { t } = useI18n();
  const allowed = permissions.includes(capability);

  useEffect(() => {
    if (!isLoading && !allowed) {
      router.replace("/");
    }
  }, [isLoading, allowed, router]);

  if (isLoading) {
    return <p className="page">{t("common.loading")}</p>;
  }
  if (!allowed) {
    return null;
  }
  return <>{children}</>;
}
