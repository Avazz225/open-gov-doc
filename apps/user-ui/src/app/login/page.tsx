"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import { ApiError, useAuth } from "@/lib/auth-context";
import { getSsoConfig, oidcAuthorize } from "@/lib/api";

// SSO/automatischer Login (Post-Roadmap-Feature): der Speicherschlüssel für
// den `state`-Wert, den `login/callback/page.tsx` gegen den von Keycloak
// zurückgegebenen `state` prüft (CSRF-/Replay-Schutz) - `sessionStorage`
// statt `localStorage`, da der Wert nur für den Dauer des Redirect-Umwegs
// gebraucht wird, nicht über Tabs/Neustarts hinweg.
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

  // SSO/automatischer Login (Post-Roadmap-Feature): vor dem Formular ein
  // stiller Versuch, ob eine installationsweite SSO-Konfiguration aktiv ist -
  // besitzt der Rechner ein gültiges Kerberos-Ticket, meldet Keycloaks
  // SPNEGO-Mechanismus automatisch an, ohne dass dieses Formular je sichtbar
  // wird; andernfalls zeigt Keycloak selbst sein Formular (kein Bruch). Kein
  // erneuter Versuch nach einem bereits fehlgeschlagenen Anlauf
  // (`?ssoError=1`, von `login/callback/page.tsx` gesetzt) - sonst
  // entstünde eine Endlosschleife aus Redirects.
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
        // SSO-Konfiguration nicht abrufbar - bleibt beim Passwort-Formular,
        // kein Fehler an dieser Stelle sichtbar (identisches Prinzip wie
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
