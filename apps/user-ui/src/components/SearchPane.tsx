"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  type DocumentSummary,
  type FacetObjectType,
  type LayoutData,
  type SearchFacets,
  type SearchResult,
  getObjectTypeLayout,
  getSearchFacets,
  searchDocuments,
} from "@/lib/api";
import { LayoutFormFields } from "./LayoutFormFields";

const RANGE_TYPES = new Set(["date", "decimal", "integer"]);

// Fällt zurück auf ein Ein-Attribut-pro-Zeile-Layout (identisch zur
// Anordnung vor P5b-S4), falls das "search"-Layout des gewählten Objekttyps
// nicht geladen werden kann - die Suche soll nicht wegen eines Ausfalls des
// Object-Type Service komplett unbenutzbar werden.
function fallbackLayout(objectType: FacetObjectType | undefined): LayoutData {
  return {
    rows: (objectType?.attributes ?? []).map((attribute) => ({
      columns: [{ attribute: attribute.name, label: attribute.name, required: false }],
    })),
    responsive_breakpoint_px: 600,
    is_custom: false,
  };
}

// Linke Spalte im "Suche"-Ansichtsmodus (Nutzerwunsch, P5-S4) - ersetzt
// ExplorerPane/MetadataPanel, während PreviewPane unverändert vom aktiven Tab
// gesteuert bleibt (siehe DocumentWorkspace). Objekttyp-Auswahl blendet
// passende Attributfilter ein, seit P5b-S4 angeordnet über das "search"-
// Formular-Layout des Objekttyps (2.2b) statt einer festen Ein-Feld-pro-
// Zeile-Reihenfolge - Bereichsfilter bei date/decimal/integer, Exakt-Match
// sonst. Klick auf ein Ergebnis öffnet es wie jedes andere Dokument über die
// bestehende Tab-/Vorschau-Maschinerie.
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
  const [layout, setLayout] = useState<LayoutData | null>(null);
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

  useEffect(() => {
    if (!token || !selectedObjectType) {
      setLayout(null);
      return;
    }
    getObjectTypeLayout(token, selectedObjectType.id, "search")
      .then(setLayout)
      .catch(() => setLayout(fallbackLayout(selectedObjectType)));
  }, [token, selectedObjectType]);

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
        <p className="hint search-syntax-hint">{t("search.syntaxHint")}</p>
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

        {selectedObjectType && layout && (
          <LayoutFormFields
            layout={layout}
            renderField={(field) => {
              const attribute = selectedObjectType.attributes.find((a) => a.name === field.attribute);
              const type = attribute?.type ?? "string";
              return RANGE_TYPES.has(type) ? (
                <fieldset className="search-attr-range">
                  <legend>{field.label}</legend>
                  <input
                    type="text"
                    aria-label={`${field.label} ${t("search.rangeFrom")}`}
                    placeholder={t("search.rangeFrom")}
                    value={attrRangeValues[field.attribute]?.gte ?? ""}
                    onChange={(event) =>
                      setAttrRangeValues((prev) => ({
                        ...prev,
                        [field.attribute]: {
                          ...prev[field.attribute],
                          gte: event.target.value,
                          lte: prev[field.attribute]?.lte ?? "",
                        },
                      }))
                    }
                  />
                  <input
                    type="text"
                    aria-label={`${field.label} ${t("search.rangeTo")}`}
                    placeholder={t("search.rangeTo")}
                    value={attrRangeValues[field.attribute]?.lte ?? ""}
                    onChange={(event) =>
                      setAttrRangeValues((prev) => ({
                        ...prev,
                        [field.attribute]: {
                          ...prev[field.attribute],
                          lte: event.target.value,
                          gte: prev[field.attribute]?.gte ?? "",
                        },
                      }))
                    }
                  />
                </fieldset>
              ) : (
                <label className="search-attr-exact">
                  {field.label}
                  <input
                    type="text"
                    value={attrValues[field.attribute] ?? ""}
                    onChange={(event) =>
                      setAttrValues((prev) => ({ ...prev, [field.attribute]: event.target.value }))
                    }
                  />
                </label>
              );
            }}
          />
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
