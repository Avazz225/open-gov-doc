"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";

// Tiefen-Verteidigung für domänengetrennte Admin-Rollen (4.6, P6-S5):
// `AdminSidebar` blendet Einträge ohne die nötige Capability zwar bereits
// aus, aber ein direkt aufgerufener Link muss serverunabhängig (statischer
// Export, kein Middleware-Äquivalent, siehe RequireAuth) trotzdem geschützt
// sein. Setzt voraus, dass die Seite bereits innerhalb von `<RequireAuth>`
// liegt - `permissions` ist erst nach erfolgreichem Login/Session-Restore
// aussagekräftig.
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
