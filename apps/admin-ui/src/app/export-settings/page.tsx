"use client";

import { AdminShell } from "@/components/AdminShell";
import { ExportSettings } from "@/components/ExportSettings";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { useI18n } from "@/i18n";

export default function ExportSettingsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.document_config">
        <AdminShell title={t("exportSettings.pageTitle")}>
          <ExportSettings />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
