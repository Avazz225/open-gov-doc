"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { UserManagement } from "@/components/UserManagement";
import { useI18n } from "@/i18n";

export default function UsersPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.user_management">
        <AdminShell title={t("users.pageTitle")}>
          <UserManagement />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
