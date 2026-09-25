"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  getCase,
  getDocument,
  getFolder,
  listFavorites,
  removeFavorite,
  type Case,
  type DocumentSummary,
  type Favorite,
  type Folder,
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

  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50";

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
              <li className="entry-row" key={favorite.id}>
                <span className="entry-name">
                  {typeLabel} {name}
                </span>
                <span className="flex gap-2">
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
    </section>
  );
}
