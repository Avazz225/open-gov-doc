"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { UserTracking } from "@/components/UserTracking";
import { useI18n } from "@/i18n";

export default function UserTrackingPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability={["admin.user_tracking", "admin.user_tracking_view"]}>
        <AdminShell title={t("userTracking.pageTitle")}>
          <UserTracking />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
