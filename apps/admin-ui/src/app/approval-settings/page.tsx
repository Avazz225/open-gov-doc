"use client";

import { AdminShell } from "@/components/AdminShell";
import { ApprovalSettings } from "@/components/ApprovalSettings";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { useI18n } from "@/i18n";

export default function ApprovalSettingsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.user_management">
        <AdminShell title={t("approvalSettings.pageTitle")}>
          <ApprovalSettings />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
