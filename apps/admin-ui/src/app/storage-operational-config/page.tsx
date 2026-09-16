"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { StorageOperationalConfig } from "@/components/StorageOperationalConfig";
import { useI18n } from "@/i18n";

export default function StorageOperationalConfigPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.storage">
        <AdminShell title={t("storageOperationalConfig.pageTitle")}>
          <StorageOperationalConfig />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
