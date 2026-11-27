"use client";

import { AdminShell } from "@/components/AdminShell";
import { ComponentInventory } from "@/components/ComponentInventory";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { useI18n } from "@/i18n";

// Software components per service (P100-S1, ADR 0271); the vulnerability
// checks built on them live on /vulnerabilities/ (P100-S3).
export default function ComponentsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.monitoring">
        <AdminShell title={t("components.pageTitle")}>
          <ComponentInventory />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
