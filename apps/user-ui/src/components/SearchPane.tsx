"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  type DocumentSummary,
  type FacetObjectType,
  type SearchFacets,
  type SearchResult,
  getSearchFacets,
  searchDocuments,
} from "@/lib/api";

const RANGE_TYPES = new Set(["date", "decimal", "integer"]);

// Linke Spalte im "Suche"-Ansichtsmodus (Nutzerwunsch, P5-S4) - ersetzt
// ExplorerPane/MetadataPanel, während PreviewPane unverändert vom aktiven Tab
// gesteuert bleibt (siehe DocumentWorkspace). Objekttyp-Auswahl blendet
// passende Attributfilter ein (Bereichsfilter bei date/decimal/integer,
// Exakt-Match sonst), Klick auf ein Ergebnis öffnet es wie jedes andere
// Dokument über die bestehende Tab-/Vorschau-Maschinerie.
export function SearchPane({
  token,
  onOpenDocument,
}: {
  token: string;
  onOpenDocument: (doc: DocumentSummary) => void;
}) {
  const { t } = useI18n();
  const [facets, setFacets] = useState<SearchFacets | null>(null);
  const [query, setQuery] = useState("");
  const [objectTypeId, setObjectTypeId] = useState<string>("");
  const [attrValues, setAttrValues] = useState<Record<string, string>>({});
  const [attrRangeValues, setAttrRangeValues] = useState<Record<string, { gte: string; lte: string }>>(
    {}
  );
  const [results, setResults] = useState<SearchResult[]>([]);
  const [hasSearched, setHasSearched] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    getSearchFacets(token)
      .then(setFacets)
      .catch(() => setFacets({ object_types: [] }));
  }, [token]);

  const selectedObjectType: FacetObjectType | undefined = facets?.object_types.find(
    (ot) => String(ot.id) === objectTypeId
  );

  function handleObjectTypeChange(value: string) {
    setObjectTypeId(value);
    setAttrValues({});
    setAttrRangeValues({});
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!token) return;
    setIsLoading(true);
    setError(null);
    setHasSearched(true);
    try {
      const attrFilters: Record<string, string> = {};
      for (const [name, value] of Object.entries(attrValues)) {
        if (value) attrFilters[`attr.${name}`] = value;
      }
      for (const [name, range] of Object.entries(attrRangeValues)) {
        if (range.gte) attrFilters[`attr.${name}.gte`] = range.gte;
        if (range.lte) attrFilters[`attr.${name}.lte`] = range.lte;
      }
      const response = await searchDocuments(token, {
        q: query || undefined,
        objectTypeId: objectTypeId ? Number(objectTypeId) : undefined,
        attrFilters,
      });
      setResults(response.results);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("search.loadError"));
      setResults([]);
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <section className="search-pane" aria-label={t("search.paneLabel")}>
      <form onSubmit={handleSubmit} className="search-form">
        <input
          type="text"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t("search.placeholder")}
          aria-label={t("search.queryLabel")}
        />
        <label className="search-object-type">
          {t("search.objectTypeLabel")}
          <select value={objectTypeId} onChange={(event) => handleObjectTypeChange(event.target.value)}>
            <option value="">{t("search.allObjectTypes")}</option>
            {facets?.object_types.map((ot) => (
              <option key={ot.id} value={ot.id}>
                {ot.name}
              </option>
            ))}
          </select>
        </label>

        {selectedObjectType?.attributes.map((attribute) =>
          RANGE_TYPES.has(attribute.type ?? "string") ? (
            <fieldset key={attribute.name} className="search-attr-range">
              <legend>{attribute.name}</legend>
              <input
                type="text"
                aria-label={`${attribute.name} ${t("search.rangeFrom")}`}
                placeholder={t("search.rangeFrom")}
                value={attrRangeValues[attribute.name]?.gte ?? ""}
                onChange={(event) =>
                  setAttrRangeValues((prev) => ({
                    ...prev,
                    [attribute.name]: { ...prev[attribute.name], gte: event.target.value, lte: prev[attribute.name]?.lte ?? "" },
                  }))
                }
              />
              <input
                type="text"
                aria-label={`${attribute.name} ${t("search.rangeTo")}`}
                placeholder={t("search.rangeTo")}
                value={attrRangeValues[attribute.name]?.lte ?? ""}
                onChange={(event) =>
                  setAttrRangeValues((prev) => ({
                    ...prev,
                    [attribute.name]: { ...prev[attribute.name], lte: event.target.value, gte: prev[attribute.name]?.gte ?? "" },
                  }))
                }
              />
            </fieldset>
          ) : (
            <label key={attribute.name} className="search-attr-exact">
              {attribute.name}
              <input
                type="text"
                value={attrValues[attribute.name] ?? ""}
                onChange={(event) =>
                  setAttrValues((prev) => ({ ...prev, [attribute.name]: event.target.value }))
                }
              />
            </label>
          )
        )}

        <button type="submit">{t("search.submit")}</button>
      </form>

      {error && <p className="error-text">{error}</p>}
      {isLoading && <p className="empty-state">{t("search.loading")}</p>}
      {!isLoading && hasSearched && results.length === 0 && (
        <p className="empty-state">{t("search.noResults")}</p>
      )}

      <ul className="search-results">
        {results.map((result) => (
          <li key={result.id}>
            <button type="button" className="search-result" onClick={() => onOpenDocument(result)}>
              <span className="search-result-title">{result.title}</span>
              {result.folder_name && (
                <span className="search-result-folder">{result.folder_name}</span>
              )}
              {result.snippet && <span className="search-result-snippet">{result.snippet}</span>}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
