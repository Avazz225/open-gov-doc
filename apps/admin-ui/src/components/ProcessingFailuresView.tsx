"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  listHandovers,
  listNotifications,
  listOcrResults,
  listRenditions,
  retryHandover,
  retryNotification,
  retryOcrResult,
  retryRendition,
  type Handover,
  type Notification,
  type OcrResult,
  type Rendition,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const FAILED_PERMANENT = "failed_permanent";

const secondaryBtn =
  "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50";
const fieldInput =
  "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";

// Processing failure visibility (Post-Roadmap Phase 20 Session 7) - pure
// visibility into `failed_permanent` records from three independent
// services + manual restart, analogous to `ArchivalTransfersView`'s
// Document/Case sections. Deliberately standalone sections (no shared
// generic hook) - same "lightweight duplication instead of abstraction"
// principle as the poll loops of this phase's associated backend
// services. `HandoverFailuresSection` (Phase 40 Session 3) added a fourth
// - federation-hub-service, unlike the other three, is reached directly
// rather than through the gateway (see lib/api.ts's `federationHubRequest`).
export function ProcessingFailuresView() {
  return (
    <div>
      <NotificationFailuresSection />
      <RenditionFailuresSection />
      <OcrResultFailuresSection />
      <HandoverFailuresSection />
    </div>
  );
}

function NotificationFailuresSection() {
  const { accessToken, permissions } = useAuth();
  const { t } = useI18n();
  const [items, setItems] = useState<Notification[]>([]);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  // P63-S3: unlike the three sibling sections below, `GET /notifications`
  // requires a real, non-"everyone" capability (`admin.notification_read`,
  // P59-S1) - previously this section had no client-side gate at all, so a
  // caller without it only ever saw the backend's raw 403 in the inline
  // error text. Checked here, not by wrapping the whole page in
  // `RequireCapability` - `ProcessingFailuresView` aggregates four
  // independent sections with different backend access models (the other
  // three read `ocr.read`/`rendition`-equivalent capabilities still
  // granted to "everyone"), so gating the whole page on this one
  // capability would incorrectly hide the other three sections from a
  // caller who can see everything except notifications.
  const hasPermission = permissions.includes("admin.notification_read");

  const reload = useCallback(async () => {
    if (!hasPermission) {
      setIsLoading(false);
      return;
    }
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
  }, [accessToken, hasPermission]);

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

  if (!hasPermission) {
    return (
      <div className="rounded-lg border border-border p-4 mb-6">
        <h2>{t("processingFailures.notificationHeading")}</h2>
        <p className="italic opacity-70">{t("processingFailures.notificationMissingPermission")}</p>
      </div>
    );
  }
  if (isLoading) return <p>{t("common.loading")}</p>;
  if (unreachable) {
    return <p className="italic opacity-70">{t("processingFailures.notificationUnreachable")}</p>;
  }

  return (
    <div className="rounded-lg border border-border p-4 mb-6">
      <h2>{t("processingFailures.notificationHeading")}</h2>
      <p className="text-sm opacity-80">{t("processingFailures.notificationHint")}</p>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
      {items.length === 0 ? (
        <p className="italic opacity-70">{t("processingFailures.notificationEmpty")}</p>
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
                    className={secondaryBtn}
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
    return <p className="italic opacity-70">{t("processingFailures.renditionUnreachable")}</p>;
  }

  return (
    <div className="rounded-lg border border-border p-4 mb-6">
      <h2>{t("processingFailures.renditionHeading")}</h2>
      <p className="text-sm opacity-80">{t("processingFailures.renditionHint")}</p>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
      {items.length === 0 ? (
        <p className="italic opacity-70">{t("processingFailures.renditionEmpty")}</p>
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
                    className={secondaryBtn}
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
    return <p className="italic opacity-70">{t("processingFailures.ocrUnreachable")}</p>;
  }

  return (
    <div className="rounded-lg border border-border p-4 mb-6">
      <h2>{t("processingFailures.ocrHeading")}</h2>
      <p className="text-sm opacity-80">{t("processingFailures.ocrHint")}</p>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
      {items.length === 0 ? (
        <p className="italic opacity-70">{t("processingFailures.ocrEmpty")}</p>
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
                    className={secondaryBtn}
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

const HANDOVER_FAILURE_STATUSES = ["delivery_failed", "result_delivery_failed"] as const;

function HandoverFailuresSection() {
  const { t } = useI18n();
  const [items, setItems] = useState<Handover[]>([]);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  // Phase 44 Session 1/ADR 0162: `POST /retry` is now gated by the hub's
  // operator secret, same as revoke - kept only in component state (never
  // persisted) since this UI has no other place to source it from and the
  // secret is meant for a human operator to type in, not to be cached.
  const [operatorKey, setOperatorKey] = useState("");

  // No accessToken guard here - unlike the three sections above,
  // federation-hub-service isn't gated by an admin JWT at all (it has no
  // admin-token model, see lib/api.ts's `federationHubRequest`), so this
  // section can load independently of the auth state.
  const reload = useCallback(async () => {
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      const lists = await Promise.all(
        HANDOVER_FAILURE_STATUSES.map((status) => listHandovers(status))
      );
      setItems(lists.flat());
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setUnreachable(true);
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleRetry(item: Handover) {
    setError(null);
    setRetryingId(item.id);
    try {
      await retryHandover(item.id, operatorKey);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("processingFailures.retryError"));
    } finally {
      setRetryingId(null);
    }
  }

  if (isLoading) return <p>{t("common.loading")}</p>;
  if (unreachable) {
    return <p className="italic opacity-70">{t("processingFailures.handoverUnreachable")}</p>;
  }

  return (
    <div className="rounded-lg border border-border p-4 mb-6">
      <h2>{t("processingFailures.handoverHeading")}</h2>
      <p className="text-sm opacity-80">{t("processingFailures.handoverHint")}</p>
      <label>
        {t("processingFailures.handoverOperatorKey")}
        <input
          type="password"
          value={operatorKey}
          onChange={(e) => setOperatorKey(e.target.value)}
          placeholder={t("processingFailures.handoverOperatorKeyPlaceholder")}
          autoComplete="off"
          className={fieldInput}
        />
      </label>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
      {items.length === 0 ? (
        <p className="italic opacity-70">{t("processingFailures.handoverEmpty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("processingFailures.handoverFrom")}</th>
              <th>{t("processingFailures.handoverTo")}</th>
              <th>{t("processingFailures.handoverProcessType")}</th>
              <th>{t("processingFailures.handoverLeg")}</th>
              <th>{t("processingFailures.attempts")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const isResultLeg = item.status === "result_delivery_failed";
              return (
                <tr key={item.id}>
                  <td>{item.from_installation_id}</td>
                  <td>{item.to_installation_id}</td>
                  <td>{item.process_type}</td>
                  <td>
                    {isResultLeg
                      ? t("processingFailures.handoverLegResult")
                      : t("processingFailures.handoverLegForward")}
                  </td>
                  <td>{isResultLeg ? item.result_attempts : item.attempts}</td>
                  <td>
                    <button
                      type="button"
                      onClick={() => handleRetry(item)}
                      disabled={retryingId !== null || operatorKey.length === 0}
                      className={secondaryBtn}
                    >
                      {retryingId === item.id
                        ? t("common.loading")
                        : t("processingFailures.retry")}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
