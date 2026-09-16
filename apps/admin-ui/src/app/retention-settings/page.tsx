"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { RetentionSettings } from "@/components/RetentionSettings";
import { useI18n } from "@/i18n";

export default function RetentionSettingsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.retention">
        <AdminShell title={t("retentionSettings.pageTitle")}>
          <RetentionSettings />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
