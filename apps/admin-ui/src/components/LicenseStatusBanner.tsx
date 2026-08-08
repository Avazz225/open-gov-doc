"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import { getLicenseStatus, type LicenseStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const EXPIRING_SOON_THRESHOLD_DAYS = 30;

function shouldWarn(status: LicenseStatus): boolean {
  if (!status.installed || !status.valid) return true;
  if (status.limits_exceeded.length > 0) return true;
  return status.days_remaining !== null && status.days_remaining <= EXPIRING_SOON_THRESHOLD_DAYS;
}

// Lizenzstatus jederzeit sichtbar (Konzept 9.3) - Vorbild `MaintenanceBanner`
// (30s-Poll, rendert nichts im Normalfall, fail-silent bei nicht
// erreichbarem license-service, damit ein Ausfall des Lizenzdienstes nicht
// die restliche UI blockiert).
export function LicenseStatusBanner() {
  const { accessToken, permissions } = useAuth();
  const { t } = useI18n();
  const [status, setStatus] = useState<LicenseStatus | null>(null);

  useEffect(() => {
    if (!accessToken || !permissions.includes("admin.license")) return;
    let cancelled = false;

    async function poll() {
      try {
        const result = await getLicenseStatus(accessToken as string);
        if (!cancelled) setStatus(result);
      } catch {
        // Unerreichbarer license-service soll die restliche UI nicht
        // blockieren - Banner bleibt einfach aus.
      }
    }

    poll();
    const interval = setInterval(poll, 60_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [accessToken, permissions]);

  if (!status || !shouldWarn(status)) return null;

  if (!status.installed) {
    return <div className="license-banner">{t("license.bannerNotInstalled")}</div>;
  }
  if (!status.valid) {
    return <div className="license-banner">{t("license.bannerInvalid")}</div>;
  }
  if (status.limits_exceeded.length > 0) {
    return (
      <div className="license-banner">
        {t("license.bannerLimitExceeded", { dimensions: status.limits_exceeded.join(", ") })}
      </div>
    );
  }
  return (
    <div className="license-banner">
      {t("license.bannerExpiringSoon", { count: status.days_remaining ?? 0 })}
    </div>
  );
}
