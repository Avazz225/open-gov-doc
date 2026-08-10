"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import { getMaintenanceStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Not-Shutdown (4.8, P6-S6): "von jedem regulären UI-Zugriff klar
// signalisiert" - reines Status-Banner ohne Bedienelemente, identisches
// Muster wie process-designer/admin-ui/user-ui.
export function MaintenanceBanner() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [active, setActive] = useState(false);

  useEffect(() => {
    if (!accessToken) return;
    let cancelled = false;

    async function poll() {
      try {
        const status = await getMaintenanceStatus(accessToken as string);
        if (!cancelled) setActive(status.active);
      } catch {
        // Unerreichbarer permission-service soll die restliche UI nicht
        // blockieren - Banner bleibt einfach aus.
      }
    }

    poll();
    const interval = setInterval(poll, 30_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [accessToken]);

  if (!active) return null;
  return <div className="maintenance-banner">{t("maintenanceBanner.text")}</div>;
}
