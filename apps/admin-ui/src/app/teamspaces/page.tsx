"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { TeamspacesAdmin } from "@/components/TeamspacesAdmin";
import { useI18n } from "@/i18n";

export default function TeamspacesPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.teamspace_management">
        <AdminShell title={t("teamspacesAdmin.pageTitle")}>
          <TeamspacesAdmin />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
