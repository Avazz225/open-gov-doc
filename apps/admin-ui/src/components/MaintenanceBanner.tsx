"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import { getMaintenanceStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Emergency shutdown (4.8, P6-S6): "clearly signaled from every regular UI
// access" - poll interval deliberately coarse (30s), since a delay of a few
// seconds in the display is uncritical compared to the actual lock, which
// already takes effect immediately server-side (gateway/auth-service, see
// ADR 0024). Renders nothing unless maintenance mode is actually active (no
// empty banner during normal operation).
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
        // An unreachable permission-service should not block the rest of
        // the UI - the banner simply stays off.
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
