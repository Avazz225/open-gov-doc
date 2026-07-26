"use client";

import { AdminShell } from "@/components/AdminShell";
import { RegistryOverview } from "@/components/RegistryOverview";
import { RequireAuth } from "@/components/RequireAuth";
import { useI18n } from "@/i18n";

export default function RegistryPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("registry.pageTitle")}>
        <RegistryOverview />
      </AdminShell>
    </RequireAuth>
  );
}
