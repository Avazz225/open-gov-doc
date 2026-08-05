"use client";

import { AdminShell } from "@/components/AdminShell";
import { AuditTraceSettings } from "@/components/AuditTraceSettings";
import { RequireAuth } from "@/components/RequireAuth";
import { useI18n } from "@/i18n";

export default function AuditTraceSettingsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("auditTraceSettings.pageTitle")}>
        <AuditTraceSettings />
      </AdminShell>
    </RequireAuth>
  );
}
