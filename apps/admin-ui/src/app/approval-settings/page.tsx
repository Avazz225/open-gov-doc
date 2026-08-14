"use client";

import { AdminShell } from "@/components/AdminShell";
import { ApprovalSettings } from "@/components/ApprovalSettings";
import { RequireAuth } from "@/components/RequireAuth";
import { useI18n } from "@/i18n";

export default function ApprovalSettingsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("approvalSettings.pageTitle")}>
        <ApprovalSettings />
      </AdminShell>
    </RequireAuth>
  );
}
