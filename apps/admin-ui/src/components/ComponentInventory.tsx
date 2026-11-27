"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  type ComponentRow,
  type ComponentSummary,
  type ServiceComponents,
  exportComponents,
  getServiceComponents,
  listComponentSummaries,
  searchComponents,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Software components (P100-S1, ADR 0271): which libraries run where. The
// table shows one row per service; a row opens its components. The search
// answers "who runs urllib3, in which version?" across all services. Export
// is CycloneDX for external scanners. CVE checks build on this (P100-S2).

const card = "rounded-lg border border-border p-4 mb-6";
const fieldInput =
  "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none focus:border-accent focus:ring-2 focus:ring-accent-bg";
const plainButton =
  "rounded-md border border-border bg-transparent px-2 py-1 text-sm text-fg hover:bg-hover-bg disabled:opacity-50 whitespace-nowrap";
const primaryButton =
  "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";

function download(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function ComponentInventory() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [summaries, setSummaries] = useState<ComponentSummary[] | null>(null);
  const [open, setOpen] = useState<ServiceComponents | null>(null);
  const [filter, setFilter] = useState("");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ComponentRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    async (refresh = false) => {
      if (!accessToken) return;
      setBusy(true);
      setError(null);
      try {
        setSummaries(await listComponentSummaries(accessToken, refresh));
      } catch (err) {
        setError(err instanceof ApiError ? err.message : t("common.loadError"));
      } finally {
        setBusy(false);
      }
    },
    [accessToken, t]
  );

  useEffect(() => {
    void load();
  }, [load]);

  async function toggle(service: string) {
    if (!accessToken) return;
    if (open?.service === service) {
      setOpen(null);
      return;
    }
    try {
      setFilter("");
      setOpen(await getServiceComponents(accessToken, service));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    }
  }

  async function runSearch(event: React.FormEvent) {
    event.preventDefault();
    if (!accessToken || query.trim().length < 2) return;
    try {
      setResults(await searchComponents(accessToken, query.trim()));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    }
  }

  async function runExport(service?: string) {
    if (!accessToken) return;
    try {
      download(await exportComponents(accessToken, service), `${service ?? "og-doc"}.cdx.json`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    }
  }

  const yesNo = (value: boolean | null) =>
    value === null ? t("components.unknown") : value ? t("components.yes") : t("components.no");

  return (
    <>
      <p className="text-sm opacity-80">{t("components.hint")}</p>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      <section className={card} aria-labelledby="components-search-heading">
        <h2 id="components-search-heading">{t("components.search")}</h2>
        <form className="flex flex-wrap items-end gap-2" onSubmit={runSearch}>
          <label className="flex min-w-0 flex-col gap-1 text-sm">
            {t("components.search")}
            <input
              className={`${fieldInput} w-72 max-w-full`}
              value={query}
              placeholder={t("components.searchPlaceholder")}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <button type="submit" className={primaryButton} disabled={query.trim().length < 2}>
            {t("components.search")}
          </button>
        </form>
        {results !== null &&
          (results.length === 0 ? (
            <p className="text-sm">{t("components.searchEmpty")}</p>
          ) : (
            <div className="mt-2 overflow-x-auto">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="text-left">
                    <th className="py-1 pr-3">{t("components.name")}</th>
                    <th className="py-1 pr-3">{t("components.version")}</th>
                    <th className="py-1 pr-3">{t("components.ecosystem")}</th>
                    <th className="py-1">{t("components.service")}</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((row) => (
                    <tr key={`${row.service}-${row.purl}`} className="border-t border-border">
                      <td className="py-1 pr-3 break-all">{row.name}</td>
                      <td className="py-1 pr-3">{row.version}</td>
                      <td className="py-1 pr-3">{row.ecosystem}</td>
                      <td className="py-1">{row.service}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
      </section>

      <section className={card} aria-labelledby="components-services-heading">
        <h2 id="components-services-heading">{t("components.pageTitle")}</h2>
        <div className="mb-2 flex flex-wrap gap-2">
          <button
            type="button"
            className={plainButton}
            disabled={busy}
            onClick={() => void load(true)}
          >
            {t("components.refresh")}
          </button>
          <button type="button" className={plainButton} onClick={() => void runExport()}>
            {t("components.export")}
          </button>
        </div>
        {summaries && (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="text-left">
                  <th className="py-1 pr-3">{t("components.service")}</th>
                  <th className="py-1 pr-3">{t("components.kind")}</th>
                  <th className="py-1 pr-3">{t("components.count")}</th>
                  <th className="py-1 pr-3">{t("components.ecosystems")}</th>
                  <th className="py-1 pr-3">{t("components.replicas")}</th>
                  <th className="py-1 pr-3">{t("components.image")}</th>
                  <th className="py-1" />
                </tr>
              </thead>
              <tbody>
                {summaries.map((row) => (
                  <Fragment key={row.service}>
                    <tr className="border-t border-border">
                      <td className="py-1 pr-3">{row.service}</td>
                      <td className="py-1 pr-3">
                        {row.kind === "frontend"
                          ? t("components.kindFrontend")
                          : t("components.kindService")}
                      </td>
                      <td className="py-1 pr-3">{row.error ? row.error : row.component_count}</td>
                      <td className="py-1 pr-3">
                        {Object.entries(row.ecosystems)
                          .map(([name, count]) => `${name} ${count}`)
                          .join(", ")}
                      </td>
                      <td
                        className={`py-1 pr-3 ${row.replicas_consistent ? "" : "text-danger font-semibold"}`}
                      >
                        {yesNo(row.replicas_consistent)}
                      </td>
                      <td
                        className={`py-1 pr-3 ${row.matches_image === false ? "text-danger font-semibold" : ""}`}
                      >
                        {yesNo(row.matches_image)}
                      </td>
                      <td className="py-1 text-right">
                        <button
                          type="button"
                          className={plainButton}
                          aria-expanded={open?.service === row.service}
                          disabled={row.component_count === 0}
                          onClick={() => void toggle(row.service)}
                        >
                          {open?.service === row.service
                            ? t("components.hide")
                            : t("components.show")}
                        </button>
                      </td>
                    </tr>
                    {open?.service === row.service && (
                      <tr>
                        <td colSpan={7} className="py-2">
                          {!open.replicas_consistent && (
                            <p className="text-sm text-danger">{t("components.noteReplicas")}</p>
                          )}
                          {open.matches_image === false && (
                            <p className="text-sm text-danger">{t("components.noteImage")}</p>
                          )}
                          <div className="mb-2 flex flex-wrap items-end gap-2">
                            <label className="flex flex-col gap-1 text-sm">
                              {t("components.filter")}
                              <input
                                className={`${fieldInput} w-56 max-w-full`}
                                value={filter}
                                onChange={(e) => setFilter(e.target.value)}
                              />
                            </label>
                            <button
                              type="button"
                              className={plainButton}
                              onClick={() => void runExport(row.service)}
                            >
                              {t("components.exportService")}
                            </button>
                          </div>
                          <div className="max-h-96 overflow-auto">
                            <table className="w-full border-collapse text-sm">
                              <thead>
                                <tr className="text-left">
                                  <th className="py-1 pr-3">{t("components.name")}</th>
                                  <th className="py-1 pr-3">{t("components.version")}</th>
                                  <th className="py-1 pr-3">{t("components.ecosystem")}</th>
                                  <th className="py-1">{t("components.own")}</th>
                                </tr>
                              </thead>
                              <tbody>
                                {open.components
                                  .filter(
                                    (c) =>
                                      !filter || c.name.toLowerCase().includes(filter.toLowerCase())
                                  )
                                  .map((c) => (
                                    <tr
                                      key={`${c.purl ?? c.name}`}
                                      className="border-t border-border"
                                    >
                                      <td className="py-1 pr-3 break-all">{c.name}</td>
                                      <td className="py-1 pr-3 break-all">{c.version}</td>
                                      <td className="py-1 pr-3">{c.ecosystem}</td>
                                      <td className="py-1">{c.own ? t("components.yes") : ""}</td>
                                    </tr>
                                  ))}
                              </tbody>
                            </table>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
