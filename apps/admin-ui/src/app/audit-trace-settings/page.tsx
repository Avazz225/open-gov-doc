"use client";

import { AdminShell } from "@/components/AdminShell";
import { AuditTraceSettings } from "@/components/AuditTraceSettings";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { useI18n } from "@/i18n";

export default function AuditTraceSettingsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.document_config">
        <AdminShell title={t("auditTraceSettings.pageTitle")}>
          <AuditTraceSettings />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
