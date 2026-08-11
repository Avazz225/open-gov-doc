"use client";

import { AdminShell } from "@/components/AdminShell";
import { ConfigPackages } from "@/components/ConfigPackages";
import { RequireAuth } from "@/components/RequireAuth";
import { useI18n } from "@/i18n";

export default function ConfigPackagesPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("configPackages.pageTitle")}>
        <ConfigPackages />
      </AdminShell>
    </RequireAuth>
  );
}
