"use client";

import { AdminShell } from "@/components/AdminShell";
import { ConfigCompare } from "@/components/ConfigCompare";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { useI18n } from "@/i18n";

export default function ConfigComparePage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.object_config">
        <AdminShell title={t("configCompare.pageTitle")}>
          <ConfigCompare />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
