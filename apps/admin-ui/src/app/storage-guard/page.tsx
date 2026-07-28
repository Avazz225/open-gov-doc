"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { StorageGuard } from "@/components/StorageGuard";
import { useI18n } from "@/i18n";

export default function StorageGuardPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("storageGuard.pageTitle")}>
        <StorageGuard />
      </AdminShell>
    </RequireAuth>
  );
}
