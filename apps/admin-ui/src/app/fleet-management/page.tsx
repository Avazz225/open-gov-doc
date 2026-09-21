"use client";

import { AdminShell } from "@/components/AdminShell";
import { FleetManagementView } from "@/components/FleetManagementView";
import { RequireAuth } from "@/components/RequireAuth";
import { useI18n } from "@/i18n";

export default function FleetManagementPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("fleetManagement.pageTitle")}>
        <FleetManagementView />
      </AdminShell>
    </RequireAuth>
  );
}
