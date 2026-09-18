"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { ReportsView } from "@/components/ReportsView";
import { useI18n } from "@/i18n";

export default function ReportsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="reporting.read">
        <AdminShell title={t("reports.pageTitle")}>
          <ReportsView />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
