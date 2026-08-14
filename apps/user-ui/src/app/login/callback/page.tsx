"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useI18n } from "@/i18n";
import { oidcCallback } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// SSO/automatic login (post-roadmap feature): return path from Keycloak's
// redirect (`GET /oidc/authorize` in login/page.tsx) - reads `code`/`state`
// from the URL, exchanges the code server-side for tokens (`POST
// /oidc/callback`) and applies the session via the same mechanism as
// a regular form login (`applySession`). Deliberately a standalone
// client page instead of middleware/SSR (static export, no server that
// could process a redirect callback server-side, concept 8).
const SSO_STATE_KEY = "dms.sso.state";

export default function LoginCallbackPage() {
  const router = useRouter();
  const { t } = useI18n();
  const { applySession } = useAuth();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");
    const expectedState = window.sessionStorage.getItem(SSO_STATE_KEY);
    window.sessionStorage.removeItem(SSO_STATE_KEY);

    if (!code || !state || !expectedState || state !== expectedState) {
      router.replace("/login?ssoError=1");
      return;
    }

    const redirectUri = `${window.location.origin}/login/callback/`;
    oidcCallback(code, redirectUri)
      .then((response) => applySession(response))
      .then(() => router.replace("/"))
      .catch(() => router.replace("/login?ssoError=1"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <main className="page">
      <p>{t("login.ssoRedirecting")}</p>
    </main>
  );
}
