"use client";

import { AdGroupMappings } from "@/components/AdGroupMappings";
import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { useI18n } from "@/i18n";

export default function AdGroupMappingsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.user_management">
        <AdminShell title={t("adGroupMappings.pageTitle")}>
          <AdGroupMappings />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
