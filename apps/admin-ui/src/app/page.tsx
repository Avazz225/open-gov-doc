"use client";

import { AdminShell } from "@/components/AdminShell";
import { DashboardWidgets } from "@/components/DashboardWidgets";
import { RequireAuth } from "@/components/RequireAuth";
import { useI18n } from "@/i18n";
import { useBranding } from "@/lib/branding-context";

export default function HomePage() {
  const { t } = useI18n();
  const { productName } = useBranding();
  return (
    <RequireAuth>
      <AdminShell title={productName ?? t("home.title")}>
        <p>{t("home.hint")}</p>
        <DashboardWidgets />
      </AdminShell>
    </RequireAuth>
  );
}
