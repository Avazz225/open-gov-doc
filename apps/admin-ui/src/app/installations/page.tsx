"use client";

import { AdminShell } from "@/components/AdminShell";
import { InstallationManager } from "@/components/InstallationManager";
import { RequireAuth } from "@/components/RequireAuth";
import { useI18n } from "@/i18n";

export default function InstallationsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("installations.pageTitle")}>
        <InstallationManager />
      </AdminShell>
    </RequireAuth>
  );
}
