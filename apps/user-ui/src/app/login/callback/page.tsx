"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useI18n } from "@/i18n";
import { oidcCallback } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// SSO/automatischer Login (Post-Roadmap-Feature): Rückweg von Keycloaks
// Redirect (`GET /oidc/authorize` in login/page.tsx) - liest `code`/`state`
// aus der URL, tauscht den Code serverseitig gegen Tokens (`POST
// /oidc/callback`) und übernimmt die Sitzung über denselben Mechanismus wie
// ein regulärer Formular-Login (`applySession`). Bewusst ein eigenständiger
// Client-Page statt Middleware/SSR (statischer Export, kein Server, der
// einen Redirect-Callback serverseitig verarbeiten könnte, Konzept 8).
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
