"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import { ApiError, getPublicShareLink, publicShareLinkContentUrl, type PublicShareLink } from "@/lib/api";

// Öffentliche, unauthentifizierte Freigabelink-Seite (4.2a, P14-S10) - bewusst
// NICHT in <RequireAuth> gewrappt (anders als jede andere Seite dieser App).
// Konzept 8 nennt "bereits geplante öffentliche, nicht-personalisierte
// Ausnahmeseiten" ausdrücklich - "kein Python-Datenzugriff pro Request nötig"
// ist hier erfüllt, weil der dynamische Abruf clientseitig per JS gegen die
// öffentliche Gateway-Route läuft (siehe lib/api.ts), nicht serverseitiges
// Python-Rendering (kein SSR, reiner statischer Export wie jede andere Seite
// dieser App, siehe next.config.mjs).
export default function SharePage() {
  const { t } = useI18n();
  // `undefined` = URL noch nicht ausgewertet, `null` = ausgewertet, aber kein
  // `?token=` vorhanden - beides mit `null` zu modellieren wäre nicht von der
  // ersten Render-Runde (vor dem Effekt unten) unterscheidbar gewesen.
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
