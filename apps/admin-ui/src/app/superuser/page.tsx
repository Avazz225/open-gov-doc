"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { SuperuserBreakGlass } from "@/components/SuperuserBreakGlass";
import { useI18n } from "@/i18n";

export default function SuperuserPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("superuser.pageTitle")}>
        <SuperuserBreakGlass />
      </AdminShell>
    </RequireAuth>
  );
}
