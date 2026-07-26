"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { UserManagement } from "@/components/UserManagement";
import { useI18n } from "@/i18n";

export default function UsersPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("users.pageTitle")}>
        <UserManagement />
      </AdminShell>
    </RequireAuth>
  );
}
