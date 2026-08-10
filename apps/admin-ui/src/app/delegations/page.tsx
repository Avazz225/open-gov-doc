"use client";

import { AdminShell } from "@/components/AdminShell";
import { DelegationsAdmin } from "@/components/DelegationsAdmin";
import { RequireAuth } from "@/components/RequireAuth";
import { useI18n } from "@/i18n";

export default function DelegationsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("delegationsAdmin.pageTitle")}>
        <DelegationsAdmin />
      </AdminShell>
    </RequireAuth>
  );
}
