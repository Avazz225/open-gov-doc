"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  getDirectoryFederationStatus,
  searchDirectory,
  searchFederatedDirectory,
  type DirectoryEntry,
  type FederatedDirectoryEntry,
} from "@/lib/api";

// Kontakte (2.5/4.4/7.4, P15-S4) - Verzeichnis zum Auffinden anderer
// Mitarbeitender. Anders als Papierkorb/Quarantäne/Posteingang ist dieser
// Sonderbereich laut Konzept "lokal, immer verfügbar" - kein Rollen-Gate,
// weder hier noch am IconRail-Eintrag.
function displayName(entry: DirectoryEntry): string {
  const name = [entry.first_name, entry.last_name].filter(Boolean).join(" ");
  return name || entry.username;
}

export function KontaktePane({ token }: { token: string }) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [localResults, setLocalResults] = useState<DirectoryEntry[] | null>(null);
  const [federatedResults, setFederatedResults] = useState<FederatedDirectoryEntry[]>([]);
  const [federationEnabled, setFederationEnabled] = useState(false);
  const [includeFederated, setIncludeFederated] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    getDirectoryFederationStatus(token)
      .then((status) => setFederationEnabled(status.enabled))
      .catch(() => setFederationEnabled(false));
  }, [token]);

  async function runSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setIsLoading(true);
    setError(null);
    try {
      const local = await searchDirectory(token, query.trim());
      setLocalResults(local);
      if (includeFederated && federationEnabled) {
        setFederatedResults(await searchFederatedDirectory(token, query.trim()));
      } else {
        setFederatedResults([]);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("kontakte.searchError"));
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <section className="kontakte-pane" aria-label={t("kontakte.paneLabel")}>
      <h2 className="pane-heading">{t("kontakte.heading")}</h2>
      <p className="hint">{t("kontakte.hint")}</p>

      <form onSubmit={runSearch}>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("kontakte.searchPlaceholder")}
          aria-label={t("kontakte.searchLabel")}
        />
        {federationEnabled && (
          <label>
            <input
              type="checkbox"
              checked={includeFederated}
              onChange={(e) => setIncludeFederated(e.target.checked)}
            />
            {t("kontakte.includeFederated")}
          </label>
        )}
        <button type="submit" disabled={!query.trim() || isLoading}>
          {t("kontakte.search")}
        </button>
      </form>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : localResults === null ? null : (
        <>
          {localResults.length === 0 && federatedResults.length === 0 ? (
            <p className="empty-state">{t("kontakte.empty")}</p>
          ) : (
            <>
              {localResults.length > 0 && (
                <ul className="entry-list">
                  {localResults.map((entry) => (
                    <li className="entry-row" key={entry.id}>
                      <span className="entry-name">
                        {displayName(entry)}
                        {entry.email ? ` — ${entry.email}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              {federatedResults.length > 0 && (
                <>
                  <h3>{t("kontakte.federatedHeading")}</h3>
                  <ul className="entry-list">
                    {federatedResults.map((entry) => (
                      <li className="entry-row" key={`${entry.installation_id}:${entry.id}`}>
                        <span className="entry-name">
                          {displayName(entry)}
                          {entry.email ? ` — ${entry.email}` : ""} ({entry.installation_display_name})
                        </span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
