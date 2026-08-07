"use client";

import { AdminShell } from "@/components/AdminShell";
import { QueryConsoleView } from "@/components/QueryConsoleView";
import { RequireAuth } from "@/components/RequireAuth";
import { useI18n } from "@/i18n";

export default function QueryConsolePage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("queryConsole.pageTitle")}>
        <QueryConsoleView />
      </AdminShell>
    </RequireAuth>
  );
}
