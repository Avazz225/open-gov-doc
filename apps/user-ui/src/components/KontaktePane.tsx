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

// Contacts (2.5/4.4/7.4, P15-S4) - directory for finding other employees.
// Unlike trash/quarantine/inbox, this special area is "local, always
// available" per the concept - no role gate, neither here nor on the
// IconRail entry.
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

  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
  const fieldInput =
    "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";
  const checkbox = "h-4 w-4 rounded border-border accent-accent";

  return (
    <section className="kontakte-pane" aria-label={t("kontakte.paneLabel")}>
      <h2 className="m-0 mb-3 text-base">{t("kontakte.heading")}</h2>
      <p className="text-sm opacity-80">{t("kontakte.hint")}</p>

      <form onSubmit={runSearch}>
        <input
          type="text"
          className={fieldInput}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("kontakte.searchPlaceholder")}
          aria-label={t("kontakte.searchLabel")}
        />
        {federationEnabled && (
          <label>
            <input
              type="checkbox"
              className={checkbox}
              checked={includeFederated}
              onChange={(e) => setIncludeFederated(e.target.checked)}
            />
            {t("kontakte.includeFederated")}
          </label>
        )}
        <button type="submit" className={primaryBtn} disabled={!query.trim() || isLoading}>
          {t("kontakte.search")}
        </button>
      </form>

      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : localResults === null ? null : (
        <>
          {localResults.length === 0 && federatedResults.length === 0 ? (
            <p className="italic opacity-70">{t("kontakte.empty")}</p>
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
