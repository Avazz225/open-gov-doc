"use client";

import { AdminShell } from "@/components/AdminShell";
import { ObjectTypeEditor } from "@/components/ObjectTypeEditor";
import { RequireAuth } from "@/components/RequireAuth";
import { useI18n } from "@/i18n";

export default function ObjectTypesPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("objectTypes.pageTitle")}>
        <ObjectTypeEditor />
      </AdminShell>
    </RequireAuth>
  );
}
