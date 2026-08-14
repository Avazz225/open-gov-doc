"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  listNotifications,
  listOcrResults,
  listRenditions,
  retryNotification,
  retryOcrResult,
  retryRendition,
  type Notification,
  type OcrResult,
  type Rendition,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const FAILED_PERMANENT = "failed_permanent";

// Processing failure visibility (Post-Roadmap Phase 20 Session 7) - pure
// visibility into `failed_permanent` records from three independent
// services + manual restart, analogous to `ArchivalTransfersView`'s
// Document/Case sections. Deliberately THREE standalone sections (no
// shared generic hook) - same "lightweight duplication instead of
// abstraction" principle as the poll loops of this phase's associated
// backend services.
export function ProcessingFailuresView() {
  return (
    <div>
      <NotificationFailuresSection />
      <RenditionFailuresSection />
      <OcrResultFailuresSection />
    </div>
  );
}

function NotificationFailuresSection() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [items, setItems] = useState<Notification[]>([]);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [retryingId, setRetryingId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      setItems(await listNotifications(accessToken, FAILED_PERMANENT));
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setUnreachable(true);
      }
    } finally {
      setIsLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleRetry(item: Notification) {
    if (!accessToken) return;
    setError(null);
    setRetryingId(item.id);
    try {
      await retryNotification(accessToken, item.id);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("processingFailures.retryError"));
    } finally {
      setRetryingId(null);
    }
  }

  if (isLoading) return <p>{t("common.loading")}</p>;
  if (unreachable) {
    return <p className="empty-state">{t("processingFailures.notificationUnreachable")}</p>;
  }

  return (
    <div className="card">
      <h2>{t("processingFailures.notificationHeading")}</h2>
      <p className="hint">{t("processingFailures.notificationHint")}</p>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {items.length === 0 ? (
        <p className="empty-state">{t("processingFailures.notificationEmpty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("processingFailures.channel")}</th>
              <th>{t("processingFailures.recipient")}</th>
              <th>{t("processingFailures.attempts")}</th>
              <th>{t("processingFailures.errorMessage")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td>{item.channel}</td>
                <td>{item.recipient}</td>
                <td>{item.attempts}</td>
                <td>{item.error ?? "—"}</td>
                <td>
                  <button
                    type="button"
                    onClick={() => handleRetry(item)}
                    disabled={retryingId !== null}
                  >
                    {retryingId === item.id
                      ? t("common.loading")
                      : t("processingFailures.retry")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function RenditionFailuresSection() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [items, setItems] = useState<Rendition[]>([]);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [retryingId, setRetryingId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      setItems(await listRenditions(accessToken, FAILED_PERMANENT));
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setUnreachable(true);
      }
    } finally {
      setIsLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleRetry(item: Rendition) {
    if (!accessToken) return;
    setError(null);
    setRetryingId(item.id);
    try {
      await retryRendition(accessToken, item.id);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("processingFailures.retryError"));
    } finally {
      setRetryingId(null);
    }
  }

  if (isLoading) return <p>{t("common.loading")}</p>;
  if (unreachable) {
    return <p className="empty-state">{t("processingFailures.renditionUnreachable")}</p>;
  }

  return (
    <div className="card">
      <h2>{t("processingFailures.renditionHeading")}</h2>
      <p className="hint">{t("processingFailures.renditionHint")}</p>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {items.length === 0 ? (
        <p className="empty-state">{t("processingFailures.renditionEmpty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("processingFailures.documentId")}</th>
              <th>{t("processingFailures.versionNumber")}</th>
              <th>{t("processingFailures.renditionType")}</th>
              <th>{t("processingFailures.attempts")}</th>
              <th>{t("processingFailures.errorMessage")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td>{item.document_id}</td>
                <td>{item.version_number}</td>
                <td>{item.rendition_type}</td>
                <td>{item.attempts}</td>
                <td>{item.error_message ?? "—"}</td>
                <td>
                  <button
                    type="button"
                    onClick={() => handleRetry(item)}
                    disabled={retryingId !== null}
                  >
                    {retryingId === item.id
                      ? t("common.loading")
                      : t("processingFailures.retry")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function OcrResultFailuresSection() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [items, setItems] = useState<OcrResult[]>([]);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [retryingId, setRetryingId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      setItems(await listOcrResults(accessToken, FAILED_PERMANENT));
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setUnreachable(true);
      }
    } finally {
      setIsLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleRetry(item: OcrResult) {
    if (!accessToken) return;
    setError(null);
    setRetryingId(item.id);
    try {
      await retryOcrResult(accessToken, item.id);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("processingFailures.retryError"));
    } finally {
      setRetryingId(null);
    }
  }

  if (isLoading) return <p>{t("common.loading")}</p>;
  if (unreachable) {
    return <p className="empty-state">{t("processingFailures.ocrUnreachable")}</p>;
  }

  return (
    <div className="card">
      <h2>{t("processingFailures.ocrHeading")}</h2>
      <p className="hint">{t("processingFailures.ocrHint")}</p>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {items.length === 0 ? (
        <p className="empty-state">{t("processingFailures.ocrEmpty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("processingFailures.documentId")}</th>
              <th>{t("processingFailures.versionNumber")}</th>
              <th>{t("processingFailures.attempts")}</th>
              <th>{t("processingFailures.errorMessage")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td>{item.document_id}</td>
                <td>{item.version_number}</td>
                <td>{item.attempts}</td>
                <td>{item.error_message ?? "—"}</td>
                <td>
                  <button
                    type="button"
                    onClick={() => handleRetry(item)}
                    disabled={retryingId !== null}
                  >
                    {retryingId === item.id
                      ? t("common.loading")
                      : t("processingFailures.retry")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
