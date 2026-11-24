"use client";

import { useId, useMemo, useState, type ReactNode } from "react";
import { useI18n } from "@/i18n";

// Long lists in the admin UI (P91-S5): one shared pattern instead of a
// page-by-page variety. A list keeps its natural height up to `maxHeight`,
// then scrolls inside its own box with the table header kept in view. Above
// `filterThreshold` entries a filter field appears with "x von y".

export const DEFAULT_FILTER_THRESHOLD = 12;

// Case- and accent-insensitive "contains" over whatever text the caller
// extracts per item; an empty query matches everything.
export function useListFilter<T>(items: T[], text: (item: T) => string) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    const needle = normalize(query.trim());
    if (!needle) return items;
    return items.filter((item) => normalize(text(item)).includes(needle));
  }, [items, query, text]);
  return { query, setQuery, filtered };
}

function normalize(value: string): string {
  return value
    .toLocaleLowerCase("de")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "");
}

export function BoundedList({
  label,
  total,
  shown,
  query,
  onQueryChange,
  filterThreshold = DEFAULT_FILTER_THRESHOLD,
  maxHeight = "max-h-[32rem]",
  children,
}: {
  // Names the list for the filter field and the scroll region.
  label: string;
  total: number;
  shown?: number;
  query?: string;
  onQueryChange?: (value: string) => void;
  filterThreshold?: number;
  maxHeight?: string;
  children: ReactNode;
}) {
  const { t } = useI18n();
  const filterId = useId();
  const withFilter = onQueryChange !== undefined && total > filterThreshold;
  return (
    <div className="bounded-list min-w-0">
      {withFilter && (
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <label htmlFor={filterId} className="sr-only">
            {t("boundedList.filterLabel", { label })}
          </label>
          <input
            id={filterId}
            type="search"
            value={query ?? ""}
            onChange={(e) => onQueryChange?.(e.target.value)}
            placeholder={t("boundedList.filterPlaceholder")}
            className="box-border w-full max-w-[20rem] rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none focus:border-accent"
          />
          <span className="text-sm opacity-80" role="status">
            {t("boundedList.count", { shown: shown ?? total, total })}
          </span>
        </div>
      )}
      <div
        className={`${maxHeight} overflow-auto`}
        // A named group, not a region: the surrounding section is usually the
        // landmark already. Focusable, since scrollable boxes must be
        // reachable by keyboard.
        role="group"
        aria-label={t("boundedList.scrollLabel", { label })}
        tabIndex={0}
      >
        {children}
      </div>
      {withFilter && shown === 0 && (
        <p className="text-sm italic opacity-70">{t("boundedList.noMatch")}</p>
      )}
    </div>
  );
}
