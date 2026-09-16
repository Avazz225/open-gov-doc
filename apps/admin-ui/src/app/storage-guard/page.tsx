"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { StorageGuard } from "@/components/StorageGuard";
import { useI18n } from "@/i18n";

export default function StorageGuardPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.storage">
        <AdminShell title={t("storageGuard.pageTitle")}>
          <StorageGuard />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
