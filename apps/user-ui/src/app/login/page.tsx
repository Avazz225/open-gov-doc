"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import { ApiError, useAuth } from "@/lib/auth-context";
import { getSsoConfig, oidcAuthorize } from "@/lib/api";

// SSO/automatic login (post-roadmap feature): the storage key for
// the `state` value that `login/callback/page.tsx` checks against the
// `state` returned by Keycloak (CSRF/replay protection) - `sessionStorage`
// instead of `localStorage`, since the value is only needed for the
// duration of the redirect round trip, not across tabs/restarts.
const SSO_STATE_KEY = "dms.sso.state";

export default function LoginPage() {
  const { login, user, isLoading } = useAuth();
  const router = useRouter();
  const { t } = useI18n();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [ssoRedirecting, setSsoRedirecting] = useState(false);

  useEffect(() => {
    if (!isLoading && user) {
      router.replace("/");
    }
  }, [isLoading, user, router]);

  // SSO/automatic login (post-roadmap feature): before the form, a
  // silent attempt to check whether an installation-wide SSO configuration is
  // active - if the machine holds a valid Kerberos ticket, Keycloak's
  // SPNEGO mechanism logs in automatically without this form ever becoming
  // visible; otherwise Keycloak shows its own form (no breakage). No
  // retry after an already-failed attempt
  // (`?ssoError=1`, set by `login/callback/page.tsx`) - otherwise
  // an infinite loop of redirects would result.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("ssoError")) {
      setError(t("login.ssoError"));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (isLoading || user) return;
    const params = new URLSearchParams(window.location.search);
    if (params.get("ssoError")) return;

    let cancelled = false;
    getSsoConfig()
      .then(async (config) => {
        if (cancelled || !config.enabled) return;
        setSsoRedirecting(true);
        const state = crypto.randomUUID();
        window.sessionStorage.setItem(SSO_STATE_KEY, state);
        const redirectUri = `${window.location.origin}/login/callback/`;
        const authorizationUrl = await oidcAuthorize(redirectUri, state);
        window.location.href = authorizationUrl;
      })
      .catch(() => {
        // SSO configuration not retrievable - stays on the password form,
        // no error visible at this point (same principle as
        // handleDownload/handleOfficeLaunch in PreviewPane.tsx).
      });
    return () => {
      cancelled = true;
    };
  }, [isLoading, user]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      router.replace("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("login.error"));
    } finally {
      setSubmitting(false);
    }
  }

  if (ssoRedirecting) {
    return (
      <main className="page">
        <p>{t("login.ssoRedirecting")}</p>
      </main>
    );
  }

  return (
    <main className="page">
      <form className="login-form" onSubmit={handleSubmit}>
        <h1>{t("login.heading")}</h1>
        <label htmlFor="username">{t("login.username")}</label>
        <input
          id="username"
          name="username"
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          required
        />
        <label htmlFor="password">{t("login.password")}</label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={submitting}>
          {submitting ? t("login.submitting") : t("login.submit")}
        </button>
      </form>
    </main>
  );
}
