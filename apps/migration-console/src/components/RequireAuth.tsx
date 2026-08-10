"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";
import { Shell } from "./Shell";

// Statischer Export hat keinen Server, der Redirects vor dem Rendern
// ausführen könnte (kein Middleware-Äquivalent) - der Schutz greift daher
// clientseitig nach dem ersten Render, sobald der Auth-Zustand geladen ist.
// migration-service gated seine Endpunkte nur über die eigene Lizenzprüfung,
// keine domänengetrennte Admin-Rolle (siehe docs/services/migration-console.md
// "Autorisierung") - diese Komponente prüft nur, ob überhaupt eine gültige
// Sitzung vorliegt.
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
