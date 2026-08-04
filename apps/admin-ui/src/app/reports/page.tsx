"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { ReportsView } from "@/components/ReportsView";
import { useI18n } from "@/i18n";

export default function ReportsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("reports.pageTitle")}>
        <ReportsView />
      </AdminShell>
    </RequireAuth>
  );
}
