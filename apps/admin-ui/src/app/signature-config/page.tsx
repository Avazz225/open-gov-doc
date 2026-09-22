"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { RetimestampStatus } from "@/components/RetimestampStatus";
import { SignatureConfig } from "@/components/SignatureConfig";
import { useI18n } from "@/i18n";

export default function SignatureConfigPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.signature_config">
        <AdminShell title={t("signatureConfig.pageTitle")}>
          <SignatureConfig />
          <RetimestampStatus />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
