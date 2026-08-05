"use client";

import { AdminShell } from "@/components/AdminShell";
import { ArchivalTransfersView } from "@/components/ArchivalTransfersView";
import { RequireAuth } from "@/components/RequireAuth";
import { useI18n } from "@/i18n";

export default function ArchivalTransfersPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("archivalTransfers.pageTitle")}>
        <ArchivalTransfersView />
      </AdminShell>
    </RequireAuth>
  );
}
