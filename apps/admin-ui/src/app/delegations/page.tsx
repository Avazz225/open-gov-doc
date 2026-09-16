"use client";

import { AdminShell } from "@/components/AdminShell";
import { DelegationsAdmin } from "@/components/DelegationsAdmin";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { useI18n } from "@/i18n";

export default function DelegationsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.user_management">
        <AdminShell title={t("delegationsAdmin.pageTitle")}>
          <DelegationsAdmin />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
