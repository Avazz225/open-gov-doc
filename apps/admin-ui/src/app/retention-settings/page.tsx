"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RetentionSettings } from "@/components/RetentionSettings";
import { useI18n } from "@/i18n";

export default function RetentionSettingsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("retentionSettings.pageTitle")}>
        <RetentionSettings />
      </AdminShell>
    </RequireAuth>
  );
}
