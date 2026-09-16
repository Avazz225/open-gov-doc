"use client";

import { AdminShell } from "@/components/AdminShell";
import { QueryConsoleView } from "@/components/QueryConsoleView";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { useI18n } from "@/i18n";

export default function QueryConsolePage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.query_console">
        <AdminShell title={t("queryConsole.pageTitle")}>
          <QueryConsoleView />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
