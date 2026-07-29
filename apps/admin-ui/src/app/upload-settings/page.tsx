"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { UploadSettings } from "@/components/UploadSettings";
import { useI18n } from "@/i18n";

export default function UploadSettingsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("uploadSettings.pageTitle")}>
        <UploadSettings />
      </AdminShell>
    </RequireAuth>
  );
}
