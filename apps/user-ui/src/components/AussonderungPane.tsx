"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  downloadCaseArchivalPackage,
  listReleasedItems,
  retrieveArchivalTransfer,
  type ReleasedItem,
} from "@/lib/api";

// Records disposal access area (2.5/5.6, P15-S5) - documents/circulation
// folders that have already been disposed of but are still within the
// transition period remain searchable/viewable here, instead of being
// discoverable only indirectly via audit trail references. A purely
// filtered view onto archival-service's already-existing `released` state
// machine - no new data store, no new mutations (retrieval/package
// download call already-existing endpoints). Circulation folders have no
// automatic expiry/deletion timestamp (no `dehydrated` status, see ADR
// 0055) - `purge_at` therefore remains empty for them.
function formatDate(value: string | null, locale: string): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(locale);
}

function triggerBrowserDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function AussonderungPane({ token }: { token: string }) {
  const { t, locale } = useI18n();
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<ReleasedItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const reload = useCallback(
    async (q?: string) => {
      if (!token) return;
      setIsLoading(true);
      setError(null);
      try {
        setItems(await listReleasedItems(token, q || undefined));
      } catch (err) {
        setError(err instanceof ApiError ? err.message : t("aussonderung.loadError"));
      } finally {
        setIsLoading(false);
      }
    },
    [token, t]
  );

  useEffect(() => {
    reload();
  }, [reload]);

  function runSearch(e: React.FormEvent) {
    e.preventDefault();
    reload(query);
  }

  async function handleRetrieve(item: ReleasedItem) {
    setBusyId(item.transfer_id);
    setError(null);
    try {
      await retrieveArchivalTransfer(token, item.transfer_id);
      await reload(query);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("aussonderung.retrieveError"));
    } finally {
      setBusyId(null);
    }
  }

  async function handleDownloadPackage(item: ReleasedItem) {
    setBusyId(item.transfer_id);
    setError(null);
    try {
      const blob = await downloadCaseArchivalPackage(token, item.transfer_id);
      triggerBrowserDownload(blob, `${item.title}.zip`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("aussonderung.downloadError"));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="aussonderung-pane" aria-label={t("aussonderung.paneLabel")}>
      <h2 className="pane-heading">{t("aussonderung.heading")}</h2>
      <p className="hint">{t("aussonderung.hint")}</p>

      <form onSubmit={runSearch}>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("aussonderung.searchPlaceholder")}
          aria-label={t("aussonderung.searchLabel")}
        />
        <button type="submit" disabled={isLoading}>
          {t("aussonderung.search")}
        </button>
      </form>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : items.length === 0 ? (
        <p className="empty-state">{t("aussonderung.empty")}</p>
      ) : (
        <ul className="entry-list">
          {items.map((item) => (
            <li className="entry-row" key={item.transfer_id}>
              <span className="entry-name">
                {item.kind === "document" ? t("aussonderung.kindDocument") : t("aussonderung.kindCase")}
                {" — "}
                {item.title}
                {item.identifier ? ` (${item.identifier})` : ""}
              </span>
              <span className="entry-meta">
                {t("aussonderung.releasedAt", { date: formatDate(item.released_at, locale) })}
                {item.purge_at
                  ? ` · ${t("aussonderung.purgeAt", { date: formatDate(item.purge_at, locale) })}`
                  : ""}
              </span>
              <span className="actions">
                {item.kind === "document" ? (
                  <button
                    type="button"
                    onClick={() => handleRetrieve(item)}
                    disabled={busyId === item.transfer_id}
                  >
                    {t("aussonderung.retrieve")}
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => handleDownloadPackage(item)}
                    disabled={busyId === item.transfer_id}
                  >
                    {t("aussonderung.downloadPackage")}
                  </button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
