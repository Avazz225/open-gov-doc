"use client";

import { useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import { ApiError, searchDocuments, type SearchResult } from "@/lib/api";

// "Open from OG Doc" (3.3a) - uses the existing full-text search
// (search-service, 3.7/3.7a) instead of a separate listing endpoint.
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
    <section className="mt-4 border-t border-border pt-3" aria-label={t("documentPicker.heading")}>
      <h2 className="m-0 mb-2 text-base">{t("documentPicker.heading")}</h2>
      <form onSubmit={handleSubmit} className="flex flex-col gap-2">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("documentPicker.placeholder")}
          aria-label={t("documentPicker.placeholder")}
          className="box-border w-full rounded-md border border-border bg-bg px-2 py-1.5 text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg"
        />
        <button
          type="submit"
          disabled={disabled || isLoading}
          className="rounded-md border border-border bg-hover-bg px-3 py-1 text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t("documentPicker.searchButton")}
        </button>
      </form>
      {error && (
        <p className="text-sm text-danger" role="alert">
          {error}
        </p>
      )}
      <ul className="mt-2 mb-0 list-none p-0">
        {results.map((result) => (
          <li
            className="flex items-center justify-between gap-2 border-b border-border py-1 last:border-b-0"
            key={result.id}
          >
            <span>{result.title}</span>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onOpen(result)}
              className="rounded-md border border-border bg-hover-bg px-3 py-1 text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50"
            >
              {t("documentPicker.openButton")}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
