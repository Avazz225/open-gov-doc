"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { WorkflowInstances } from "@/components/WorkflowInstances";
import { useI18n } from "@/i18n";

export default function WorkflowInstancesPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="workflow.instance_modify">
        <AdminShell title={t("workflowInstances.pageTitle")}>
          <WorkflowInstances />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
