"use client";

import { AdminShell } from "@/components/AdminShell";
import { ComponentInventory } from "@/components/ComponentInventory";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { VulnerabilityCheck } from "@/components/VulnerabilityCheck";
import { useI18n } from "@/i18n";

// Software components per service (P100-S1, ADR 0271) and the
// vulnerability check built on them (P100-S2, ADR 0272).
export default function ComponentsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.monitoring">
        <AdminShell title={t("components.pageTitle")}>
          <VulnerabilityCheck />
          <ComponentInventory />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
