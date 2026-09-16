"use client";

import { AdminShell } from "@/components/AdminShell";
import { KennzeichenSettings } from "@/components/KennzeichenSettings";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { useI18n } from "@/i18n";

export default function KennzeichenSettingsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.object_config">
        <AdminShell title={t("kennzeichenSettings.pageTitle")}>
          <KennzeichenSettings />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
