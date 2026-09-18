"use client";

import { AdminShell } from "@/components/AdminShell";
import { ForensicTraceView } from "@/components/ForensicTraceView";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { useI18n } from "@/i18n";

export default function ForensicTracePage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="reporting.forensic_trace">
        <AdminShell title={t("forensicTrace.pageTitle")}>
          <ForensicTraceView />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
