"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  createShareLink,
  listShareLinks,
  revokeShareLink,
  type ShareLink,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

function toDateInputValue(daysFromNow: number): string {
  const date = new Date();
  date.setDate(date.getDate() + daysFromNow);
  return date.toISOString().slice(0, 10);
}

function shareUrlFor(shareToken: string): string {
  return `${window.location.origin}/share?token=${encodeURIComponent(shareToken)}`;
}

// Öffentlicher Freigabelink (4.2a, P14-S10) - erreichbar über einen neuen
// Kontextmenü-Eintrag in ExplorerPane.tsx (nur wenn `share-link-config`
// `enabled` meldet, siehe dortiges Laden). Gleiches Modal-Muster wie
// FolderRetentionModal.tsx: Liste bestehender Links + ein Erzeugen-Formular
// in einem Dialog, `.modal-backdrop`/`.modal-content`-Klassen wiederverwendet.
export function ShareLinkModal({
  documentId,
  documentTitle,
  onClose,
}: {
  documentId: string;
  documentTitle: string;
  onClose: () => void;
}) {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [links, setLinks] = useState<ShareLink[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expiresAtInput, setExpiresAtInput] = useState(toDateInputValue(7));
  const [isCreating, setIsCreating] = useState(false);
  const [busyToken, setBusyToken] = useState<string | null>(null);

  const reload = useCallback(() => {
    if (!accessToken) return;
    setIsLoading(true);
    setError(null);
    listShareLinks(accessToken, documentId)
      .then(setLinks)
      .catch((err) => setError(err instanceof ApiError ? err.message : t("shareLinkModal.loadError")))
      .finally(() => setIsLoading(false));
  }, [accessToken, documentId, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleCreate() {
    if (!accessToken || !expiresAtInput) return;
    setIsCreating(true);
    setError(null);
    try {
      await createShareLink(accessToken, documentId, new Date(expiresAtInput).toISOString());
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("shareLinkModal.createError"));
    } finally {
      setIsCreating(false);
    }
  }

  async function handleRevoke(link: ShareLink) {
    if (!accessToken) return;
    setBusyToken(link.token);
    setError(null);
    try {
      await revokeShareLink(accessToken, link.token);
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("shareLinkModal.revokeError"));
    } finally {
      setBusyToken(null);
    }
  }

  function isActive(link: ShareLink): boolean {
    return link.revoked_at === null && new Date(link.expires_at).getTime() > Date.now();
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-content"
        role="dialog"
        aria-modal="true"
        aria-label={t("shareLinkModal.heading", { name: documentTitle })}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 className="pane-heading">{t("shareLinkModal.heading", { name: documentTitle })}</h2>
          <button type="button" className="modal-close" aria-label={t("common.close")} onClick={onClose}>
            ×
          </button>
        </div>

        <div className="inline-form">
          <label>
            {t("shareLinkModal.expiresAtLabel")}
            <input
              type="date"
              value={expiresAtInput}
              onChange={(e) => setExpiresAtInput(e.target.value)}
            />
          </label>
          <button type="button" onClick={handleCreate} disabled={isCreating || !expiresAtInput}>
            {isCreating ? t("shareLinkModal.creating") : t("shareLinkModal.create")}
          </button>
        </div>

        {isLoading ? (
          <p>{t("common.loading")}</p>
        ) : links.length === 0 ? (
          <p className="empty-state">{t("shareLinkModal.empty")}</p>
        ) : (
          <ul className="entry-list">
            {links.map((link) => (
              <li className="entry-row" key={link.token}>
                {isActive(link) ? (
                  <>
                    <input
                      className="share-link-url"
                      readOnly
                      value={shareUrlFor(link.token)}
                      onFocus={(e) => e.target.select()}
                      aria-label={t("shareLinkModal.urlLabel")}
                    />
                    <span className="hint">
                      {t("shareLinkModal.expiresAt", {
                        date: new Date(link.expires_at).toLocaleString(),
                      })}
                    </span>
                    <span className="actions">
                      <button
                        type="button"
                        onClick={() => handleRevoke(link)}
                        disabled={busyToken === link.token}
                      >
                        {t("shareLinkModal.revoke")}
                      </button>
                    </span>
                  </>
                ) : (
                  <span className="hint">
                    {link.revoked_at
                      ? t("shareLinkModal.revokedState")
                      : t("shareLinkModal.expiredState")}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}

        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
