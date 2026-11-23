"use client";

import { useState } from "react";
import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { ConformityReportPanel } from "@/components/structure/ConformityReportPanel";
import { StructureSettings } from "@/components/structure/StructureSettings";
import { TemplateManager } from "@/components/structure/TemplateManager";
import { useI18n } from "@/i18n";

// "Ablagestruktur" (P91-S3, ADR 0255): the installation-wide settings and
// overview, what the rules say about existing data, and the structure
// templates. The per-class rules are edited with the object types.
export default function StructurePage() {
  const { t } = useI18n();
  const [refreshKey, setRefreshKey] = useState(0);
  return (
    <RequireAuth>
      <RequireCapability capability="admin.object_config">
        <AdminShell title={t("structure.pageTitle")}>
          <StructureSettings onSaved={() => setRefreshKey((k) => k + 1)} />
          <section className="mb-6 rounded-lg border border-border p-4" aria-labelledby="structure-report">
            <h2 id="structure-report">{t("structure.reportHeading")}</h2>
            <p className="text-sm opacity-80">{t("structure.reportHint")}</p>
            <ConformityReportPanel kind="folders" refreshKey={refreshKey} />
            <ConformityReportPanel kind="documents" refreshKey={refreshKey} />
          </section>
          <TemplateManager />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
