"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import { ApiError, getPublicShareLink, publicShareLinkContentUrl, type PublicShareLink } from "@/lib/api";

// Public, unauthenticated share link page (4.2a, P14-S10) - deliberately
// NOT wrapped in <RequireAuth> (unlike every other page in this app).
// Concept 8 explicitly mentions "already-planned public, non-personalized
// exception pages" - "no Python data access needed per request"
// is satisfied here because the dynamic fetch runs client-side via JS against
// the public gateway route (see lib/api.ts), not server-side
// Python rendering (no SSR, pure static export like every other page
// in this app, see next.config.mjs).
export default function SharePage() {
  const { t } = useI18n();
  // `undefined` = URL not yet evaluated, `null` = evaluated, but no
  // `?token=` present - modeling both with `null` would not have been
  // distinguishable from the first render pass (before the effect below).
  const [token, setToken] = useState<string | null | undefined>(undefined);
  const [link, setLink] = useState<PublicShareLink | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setToken(params.get("token"));
  }, []);

  useEffect(() => {
    if (token === undefined) return;
    if (!token) {
      setError(t("share.missingToken"));
      setIsLoading(false);
      return;
    }
    getPublicShareLink(token)
      .then(setLink)
      .catch((err) => setError(err instanceof ApiError ? err.message : t("share.loadError")))
      .finally(() => setIsLoading(false));
  }, [token, t]);

  return (
    <main className="page share-page">
      <h1>{t("share.heading")}</h1>

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : error ? (
        <p className="error-text" role="alert">
          {error}
        </p>
      ) : link ? (
        <div className="share-card">
          <h2>{link.title}</h2>
          <p className="hint">
            {t("share.expiresAt", { date: new Date(link.expires_at).toLocaleString() })}
          </p>
          <a
            className="share-download-button"
            href={publicShareLinkContentUrl(token as string)}
            download={link.title}
          >
            {t("share.download")}
          </a>
        </div>
      ) : null}
    </main>
  );
}
