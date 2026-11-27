"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { VulnerabilityCheck } from "@/components/VulnerabilityCheck";
import { VulnerabilityScanReport } from "@/components/VulnerabilityScanReport";
import { useI18n } from "@/i18n";

// Known vulnerabilities (P100-S2/S3, ADR 0272): the scan of every installed
// component, the offline OSV database, and the check of a single id.
export default function VulnerabilitiesPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.monitoring">
        <AdminShell title={t("vulnerabilities.pageTitle")}>
          <VulnerabilityScanReport />
          <VulnerabilityCheck />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
