"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  getCase,
  getDocument,
  getFolder,
  listFavorites,
  listWatches,
  removeFavorite,
  removeWatch,
  type Case,
  type DocumentSummary,
  type Favorite,
  type Folder,
  type Watch,
} from "@/lib/api";

interface ResolvedFavorite {
  favorite: Favorite;
  document: DocumentSummary | null;
  folder: Folder | null;
  case: Case | null;
}

// Filtered view/bookmark list for quickly finding things again (since
// P7-S1d). Resolves the display name live via the document/folder service
// for each favorite instead of storing it itself - an object that was
// meanwhile renamed/moved this way always shows the current state. A 404
// (e.g. after deletion of the original) is tolerated instead of letting
// the whole list fail - the entry remains visible with placeholder text
// and removable (see docs/services/favorite-service.md, deliberately no
// referential check when creating a favorite).
export function FavoritesPane({
  token,
  currentUsername,
  onOpenDocument,
  onOpenFolder,
  onOpenCase,
}: {
  token: string;
  currentUsername: string;
  onOpenDocument: (doc: DocumentSummary) => void;
  onOpenFolder: (folder: Folder) => void;
  onOpenCase: (caseItem: Case) => void;
}) {
  const { t } = useI18n();
  const [entries, setEntries] = useState<ResolvedFavorite[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  // Watch subscriptions (P90-S4, ADR 0256), listed below the favorites.
  const [watched, setWatched] = useState<
    { watch: Watch; document: DocumentSummary | null; folder: Folder | null }[]
  >([]);

  const reload = useCallback(async () => {
    if (!token || !currentUsername) return;
    setIsLoading(true);
    setError(null);
    try {
      const favorites = await listFavorites(token, currentUsername);
      const resolved = await Promise.all(
        favorites.map(async (favorite): Promise<ResolvedFavorite> => {
          if (favorite.object_type === "document") {
            const document = await getDocument(token, favorite.object_id).catch(() => null);
            return { favorite, document, folder: null, case: null };
          }
          if (favorite.object_type === "case") {
            const caseItem = await getCase(token, favorite.object_id).catch(() => null);
            return { favorite, document: null, folder: null, case: caseItem };
          }
          const folder = await getFolder(token, favorite.object_id).catch(() => null);
          return { favorite, document: null, folder, case: null };
        })
      );
      setEntries(resolved);
      const watchList = await listWatches(token, currentUsername).catch(() => []);
      setWatched(
        await Promise.all(
          watchList.map(async (watch) =>
            watch.object_type === "document"
              ? {
                  watch,
                  document: await getDocument(token, watch.object_id).catch(() => null),
                  folder: null,
                }
              : {
                  watch,
                  document: null,
                  folder: await getFolder(token, watch.object_id).catch(() => null),
                }
          )
        )
      );
    } catch {
      setError(t("favorites.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [token, currentUsername, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleRemove(favorite: Favorite) {
    setBusyId(favorite.id);
    setError(null);
    try {
      await removeFavorite(token, {
        user_id: favorite.user_id,
        object_type: favorite.object_type,
        object_id: favorite.object_id,
      });
      await reload();
    } catch {
      setError(t("favorites.removeError"));
    } finally {
      setBusyId(null);
    }
  }

  async function handleUnwatch(watch: Watch) {
    setBusyId(watch.id);
    setError(null);
    try {
      await removeWatch(token, {
        user_id: watch.user_id,
        object_type: watch.object_type,
        object_id: watch.object_id,
      });
      await reload();
    } catch {
      setError(t("favorites.unwatchError"));
    } finally {
      setBusyId(null);
    }
  }

  // Name and actions wrap instead of overlapping at narrow widths (found at
  // 390 px in P90-S4; the shared `.entry-row` keeps them on one line).
  const ROW =
    "flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-border py-2";
  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";

  return (
    <section className="favorites-pane" aria-label={t("favorites.paneLabel")}>
      <h2 className="m-0 mb-3 text-base">{t("favorites.heading")}</h2>
      <p className="text-sm opacity-80">{t("favorites.hint")}</p>

      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : entries.length === 0 ? (
        <p className="italic opacity-70">{t("favorites.empty")}</p>
      ) : (
        <ul className="entry-list">
          {entries.map(({ favorite, document, folder, case: caseItem }) => {
            const name =
              document?.title ??
              folder?.name ??
              caseItem?.name ??
              t("favorites.unresolvedName");
            const typeLabel =
              favorite.object_type === "document"
                ? t("favorites.typeDocument")
                : favorite.object_type === "case"
                  ? t("favorites.typeCase")
                  : t("favorites.typeFolder");
            return (
              <li className={ROW} key={favorite.id}>
                <span className="min-w-0 break-words">
                  {typeLabel} {name}
                </span>
                <span className="flex flex-wrap gap-2">
                  {document && (
                    <button type="button" className={secondaryBtn} onClick={() => onOpenDocument(document)}>
                      {t("favorites.open")}
                    </button>
                  )}
                  {folder && (
                    <button type="button" className={secondaryBtn} onClick={() => onOpenFolder(folder)}>
                      {t("favorites.open")}
                    </button>
                  )}
                  {caseItem && (
                    <button type="button" className={secondaryBtn} onClick={() => onOpenCase(caseItem)}>
                      {t("favorites.open")}
                    </button>
                  )}
                  <button
                    type="button"
                    className={secondaryBtn}
                    onClick={() => handleRemove(favorite)}
                    disabled={busyId === favorite.id}
                  >
                    {t("favorites.remove")}
                  </button>
                </span>
              </li>
            );
          })}
        </ul>
      )}

      {!isLoading && (
        <section aria-labelledby="watched-heading" className="mt-6">
          <h3 id="watched-heading" className="m-0 mb-1 text-sm">
            {t("favorites.watchedHeading")}
          </h3>
          <p className="m-0 mb-2 text-sm opacity-80">{t("favorites.watchedHint")}</p>
          {watched.length === 0 ? (
            <p className="italic opacity-70">{t("favorites.watchedEmpty")}</p>
          ) : (
            <ul className="entry-list">
              {watched.map(({ watch, document, folder }) => {
                const name = document?.title ?? folder?.name ?? t("favorites.unresolvedName");
                const typeLabel =
                  watch.object_type === "document"
                    ? t("favorites.typeDocument")
                    : t("favorites.typeFolder");
                return (
                  <li className={ROW} key={watch.id}>
                    <span className="min-w-0 break-words">
                      {typeLabel} {name}
                    </span>
                    <span className="flex flex-wrap gap-2">
                      {document && (
                        <button type="button" className={secondaryBtn} onClick={() => onOpenDocument(document)}>
                          {t("favorites.open")}
                        </button>
                      )}
                      {folder && (
                        <button type="button" className={secondaryBtn} onClick={() => onOpenFolder(folder)}>
                          {t("favorites.open")}
                        </button>
                      )}
                      <button
                        type="button"
                        className={secondaryBtn}
                        onClick={() => handleUnwatch(watch)}
                        disabled={busyId === watch.id}
                      >
                        {t("favorites.unwatch")}
                      </button>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      )}
    </section>
  );
}
