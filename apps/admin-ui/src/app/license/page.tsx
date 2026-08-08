"use client";

import { AdminShell } from "@/components/AdminShell";
import { LicenseStatusView } from "@/components/LicenseStatusView";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { useI18n } from "@/i18n";

export default function LicensePage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.license">
        <AdminShell title={t("license.pageTitle")}>
          <LicenseStatusView />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
