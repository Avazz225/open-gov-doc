"use client";

import { AdminShell } from "@/components/AdminShell";
import { ArchivalTransfersView } from "@/components/ArchivalTransfersView";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { useI18n } from "@/i18n";

export default function ArchivalTransfersPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="archival.read">
        <AdminShell title={t("archivalTransfers.pageTitle")}>
          <ArchivalTransfersView />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
