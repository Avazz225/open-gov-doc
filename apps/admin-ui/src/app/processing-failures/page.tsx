"use client";

import { AdminShell } from "@/components/AdminShell";
import { ProcessingFailuresView } from "@/components/ProcessingFailuresView";
import { RequireAuth } from "@/components/RequireAuth";
import { useI18n } from "@/i18n";

export default function ProcessingFailuresPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("processingFailures.pageTitle")}>
        <ProcessingFailuresView />
      </AdminShell>
    </RequireAuth>
  );
}
