"use client";

import { AdminShell } from "@/components/AdminShell";
import { KennzeichenSettings } from "@/components/KennzeichenSettings";
import { RequireAuth } from "@/components/RequireAuth";
import { useI18n } from "@/i18n";

export default function KennzeichenSettingsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("kennzeichenSettings.pageTitle")}>
        <KennzeichenSettings />
      </AdminShell>
    </RequireAuth>
  );
}
