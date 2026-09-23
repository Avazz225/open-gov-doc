"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import { ApiError, useAuth } from "@/lib/auth-context";

// Direct links (post-roadmap feature, Phase 27, ADR 0106): only same-origin
// relative paths starting with exactly one "/" are honored - "//" or "/\\"
// are browser-recognized protocol-relative URLs, the classic open-redirect
// vector for a "returnTo" parameter.
function sanitizeReturnTo(value: string | null): string {
  if (value && /^\/(?!\/|\\)/.test(value)) {
    return value;
  }
  return "/";
}

export default function LoginPage() {
  const { login, user, isLoading } = useAuth();
  const router = useRouter();
  const { t } = useI18n();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!isLoading && user) {
      router.replace(sanitizeReturnTo(new URLSearchParams(window.location.search).get("returnTo")));
    }
  }, [isLoading, user, router]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      router.replace(sanitizeReturnTo(new URLSearchParams(window.location.search).get("returnTo")));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("login.error"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="page flex min-h-screen items-center justify-center">
      <div className="box-border w-full rounded-lg border border-border bg-surface p-6 shadow-lg">
        <h1 className="text-lg font-bold text-surface-fg">{t("login.heading")}</h1>
        <form className="mt-4 flex flex-col gap-3" onSubmit={handleSubmit}>
          <div className="flex flex-col gap-1">
            <label htmlFor="username" className="text-sm font-medium text-surface-fg">
              {t("login.username")}
            </label>
            <input
              id="username"
              name="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              className="box-border w-full rounded-md border border-border bg-bg px-3 py-2 text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="password" className="text-sm font-medium text-surface-fg">
              {t("login.password")}
            </label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="box-border w-full rounded-md border border-border bg-bg px-3 py-2 text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg"
            />
          </div>
          {error && (
            <p className="rounded-md bg-danger-bg px-3 py-2 text-sm text-danger" role="alert">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={submitting}
            className="mt-1 box-border w-full rounded-md bg-accent px-4 py-2 font-medium text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? t("login.submitting") : t("login.submit")}
          </button>
        </form>
      </div>
    </main>
  );
}
