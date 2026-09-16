"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { UploadSettings } from "@/components/UploadSettings";
import { useI18n } from "@/i18n";

export default function UploadSettingsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.document_config">
        <AdminShell title={t("uploadSettings.pageTitle")}>
          <UploadSettings />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
