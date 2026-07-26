"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { useI18n } from "@/i18n";

export default function HomePage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("home.title")}>
        <p>{t("home.hint")}</p>
      </AdminShell>
    </RequireAuth>
  );
}
