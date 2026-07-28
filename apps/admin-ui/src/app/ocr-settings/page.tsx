"use client";

import { AdminShell } from "@/components/AdminShell";
import { OcrSettings } from "@/components/OcrSettings";
import { RequireAuth } from "@/components/RequireAuth";
import { useI18n } from "@/i18n";

export default function OcrSettingsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("ocrSettings.pageTitle")}>
        <OcrSettings />
      </AdminShell>
    </RequireAuth>
  );
}
