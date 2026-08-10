"use client";

import { useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import { ApiError, searchDocuments, type SearchResult } from "@/lib/api";

// "Aus OG Doc öffnen" (3.3a) - nutzt die bestehende Volltextsuche
// (search-service, 3.7/3.7a) statt eines eigenen Auflistungs-Endpunkts.
export function DocumentPicker({
  token,
  onOpen,
  disabled,
}: {
  token: string;
  onOpen: (result: SearchResult) => void;
  disabled: boolean;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    setIsLoading(true);
    setError(null);
    try {
      setResults(await searchDocuments(token, query.trim()));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("documentPicker.searchError"));
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <section className="section" aria-label={t("documentPicker.heading")}>
      <h2>{t("documentPicker.heading")}</h2>
      <form onSubmit={handleSubmit}>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("documentPicker.placeholder")}
          aria-label={t("documentPicker.placeholder")}
        />
        <button type="submit" disabled={disabled || isLoading}>
          {t("documentPicker.searchButton")}
        </button>
      </form>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <ul className="entry-list">
        {results.map((result) => (
          <li className="entry-row" key={result.id}>
            <span>{result.title}</span>
            <button type="button" disabled={disabled} onClick={() => onOpen(result)}>
              {t("documentPicker.openButton")}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
