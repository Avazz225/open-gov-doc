"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  getDocument,
  getFolder,
  listFavorites,
  removeFavorite,
  type DocumentSummary,
  type Favorite,
  type Folder,
} from "@/lib/api";

interface ResolvedFavorite {
  favorite: Favorite;
  document: DocumentSummary | null;
  folder: Folder | null;
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
}: {
  token: string;
  currentUsername: string;
  onOpenDocument: (doc: DocumentSummary) => void;
  onOpenFolder: (folder: Folder) => void;
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
            return { favorite, document, folder: null };
          }
          const folder = await getFolder(token, favorite.object_id).catch(() => null);
          return { favorite, document: null, folder };
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

  return (
    <section className="favorites-pane" aria-label={t("favorites.paneLabel")}>
      <h2 className="pane-heading">{t("favorites.heading")}</h2>
      <p className="hint">{t("favorites.hint")}</p>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : entries.length === 0 ? (
        <p className="empty-state">{t("favorites.empty")}</p>
      ) : (
        <ul className="entry-list">
          {entries.map(({ favorite, document, folder }) => {
            const name = document?.title ?? folder?.name ?? t("favorites.unresolvedName");
            return (
              <li className="entry-row" key={favorite.id}>
                <span className="entry-name">
                  {favorite.object_type === "document"
                    ? t("favorites.typeDocument")
                    : t("favorites.typeFolder")}{" "}
                  {name}
                </span>
                <span className="actions">
                  {document && (
                    <button type="button" onClick={() => onOpenDocument(document)}>
                      {t("favorites.open")}
                    </button>
                  )}
                  {folder && (
                    <button type="button" onClick={() => onOpenFolder(folder)}>
                      {t("favorites.open")}
                    </button>
                  )}
                  <button
                    type="button"
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
