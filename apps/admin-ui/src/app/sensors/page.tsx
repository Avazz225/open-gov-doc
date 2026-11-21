"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { SensorSettings } from "@/components/SensorSettings";
import { useI18n } from "@/i18n";

export default function SensorsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("sensors.pageTitle")}>
        <SensorSettings />
      </AdminShell>
    </RequireAuth>
  );
}
