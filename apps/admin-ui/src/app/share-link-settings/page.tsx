"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { ShareLinkSettings } from "@/components/ShareLinkSettings";
import { useI18n } from "@/i18n";

export default function ShareLinkSettingsPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.document_config">
        <AdminShell title={t("shareLinkSettings.pageTitle")}>
          <ShareLinkSettings />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
