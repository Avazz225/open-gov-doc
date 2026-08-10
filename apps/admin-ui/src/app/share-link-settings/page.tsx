"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { ShareLinkSettings } from "@/components/ShareLinkSettings";
import { useI18n } from "@/i18n";

export default function ShareLinkSettingsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("shareLinkSettings.pageTitle")}>
        <ShareLinkSettings />
      </AdminShell>
    </RequireAuth>
  );
}
