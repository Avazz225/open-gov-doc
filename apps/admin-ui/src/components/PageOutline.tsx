"use client";

import { useCallback, useEffect, useState, type RefObject } from "react";
import { useI18n } from "@/i18n";

// Page outline (user request after P96-S3): every admin page is a stack of
// `<section>` cards with an `<h2>` each. The outline lists those headings so
// a long page (sensors, users, object types) can be jumped through. It reads
// the headings from the rendered page instead of a per-page list, so every
// page gets it without changes, including sections that appear after data
// loads (a MutationObserver keeps it current).

export interface OutlineItem {
  id: string;
  text: string;
}

// Distance from the top of the viewport at which a section counts as the
// current one; matches the headings' scroll-margin-top plus some slack.
const ACTIVE_OFFSET_PX = 96;

function slugify(text: string): string {
  return text
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/ß/g, "ss")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function collectHeadings(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>("h2")).filter(
    (heading) =>
      // Headings inside dialogs or the outline itself aren't page sections.
      !heading.closest('[role="dialog"], [data-page-outline]') &&
      (heading.textContent ?? "").trim() !== ""
  );
}

/** The `<h2>` sections inside `containerRef`, kept current as the page changes.
 * Headings without an id get a stable one derived from their text. */
export function usePageOutline(containerRef: RefObject<HTMLElement | null>): {
  items: OutlineItem[];
  activeId: string | null;
} {
  const [items, setItems] = useState<OutlineItem[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let frame = 0;
    const refresh = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const used = new Set<string>();
        const next = collectHeadings(container).map((heading) => {
          const text = (heading.textContent ?? "").trim();
          if (!heading.id) {
            let id = `abschnitt-${slugify(text) || "x"}`;
            for (let n = 2; used.has(id) || document.getElementById(id); n++) {
              id = `abschnitt-${slugify(text) || "x"}-${n}`;
            }
            heading.id = id;
          }
          used.add(heading.id);
          return { id: heading.id, text };
        });
        setItems((prev) =>
          prev.length === next.length &&
          prev.every((item, i) => item.id === next[i].id && item.text === next[i].text)
            ? prev
            : next
        );
      });
    };

    refresh();
    const observer = new MutationObserver(refresh);
    observer.observe(container, { childList: true, subtree: true, characterData: true });
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [containerRef]);

  useEffect(() => {
    if (items.length === 0) return;
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        let current: string | null = items[0].id;
        for (const item of items) {
          const heading = document.getElementById(item.id);
          if (heading && heading.getBoundingClientRect().top <= ACTIVE_OFFSET_PX) {
            current = item.id;
          }
        }
        // At the very bottom the last sections may never reach the offset;
        // then the last one is current.
        const atBottom =
          window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
        setActiveId(atBottom ? items[items.length - 1].id : current);
      });
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      cancelAnimationFrame(frame);
    };
  }, [items]);

  return { items, activeId };
}

function useJumpTo(): (id: string) => void {
  return useCallback((id: string) => {
    const heading = document.getElementById(id);
    if (!heading) return;
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    // Scroll to the section card around the heading, so its frame shows too.
    const target = heading.closest("section") ?? heading;
    target.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
    // Move focus for keyboard and screen-reader users without a second jump.
    if (!heading.hasAttribute("tabindex")) heading.setAttribute("tabindex", "-1");
    heading.focus({ preventScroll: true });
    window.history.replaceState(null, "", `#${id}`);
  }, []);
}

function OutlineList({
  items,
  activeId,
  onJump,
}: {
  items: OutlineItem[];
  activeId: string | null;
  onJump: (id: string) => void;
}) {
  return (
    <ul className="m-0 list-none border-l border-border p-0">
      {items.map((item) => {
        const active = item.id === activeId;
        return (
          <li key={item.id}>
            <a
              href={`#${item.id}`}
              aria-current={active ? "location" : undefined}
              onClick={(event) => {
                event.preventDefault();
                onJump(item.id);
              }}
              className={`-ml-px block border-l-2 py-1 pr-2 pl-3 text-sm no-underline ${
                active
                  ? "border-accent font-semibold text-fg"
                  : "border-transparent text-fg opacity-75 hover:bg-hover-bg hover:opacity-100"
              }`}
            >
              {item.text}
            </a>
          </li>
        );
      })}
    </ul>
  );
}

/** Wide screens: a sticky column right of the content. */
export function PageOutlineAside({
  items,
  activeId,
}: {
  items: OutlineItem[];
  activeId: string | null;
}) {
  const { t } = useI18n();
  const jump = useJumpTo();
  if (items.length < 2) return null;
  return (
    <aside className="hidden w-[220px] flex-none py-6 pr-4 xl:block" data-page-outline>
      <nav
        aria-label={t("outline.label")}
        className="sticky top-4 max-h-[calc(100vh-2rem)] overflow-y-auto"
      >
        <p className="m-0 mb-2 pl-3 text-sm font-semibold opacity-85">{t("outline.label")}</p>
        <OutlineList items={items} activeId={activeId} onJump={jump} />
      </nav>
    </aside>
  );
}

/** Narrower screens: a collapsible list above the content. */
export function PageOutlineInline({
  items,
  activeId,
}: {
  items: OutlineItem[];
  activeId: string | null;
}) {
  const { t } = useI18n();
  const jump = useJumpTo();
  if (items.length < 2) return null;
  return (
    <details className="mb-4 rounded-lg border border-border px-3 py-2 xl:hidden" data-page-outline>
      <summary className="cursor-pointer text-sm font-semibold">{t("outline.label")}</summary>
      <nav aria-label={t("outline.label")} className="mt-2">
        <OutlineList items={items} activeId={activeId} onJump={jump} />
      </nav>
    </details>
  );
}
