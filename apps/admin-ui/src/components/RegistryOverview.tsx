"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import { ApiError, type ServiceInstance, listServiceInstances } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

export function RegistryOverview() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [instances, setInstances] = useState<ServiceInstance[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    try {
      setInstances(await listServiceInstances(accessToken));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [accessToken, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  return (
    <>
      <div className="actions" style={{ marginBottom: "1rem" }}>
        <button type="button" onClick={reload}>
          {t("common.refresh")}
        </button>
      </div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("registry.serviceType")}</th>
              <th>{t("registry.instance")}</th>
              <th>{t("registry.version")}</th>
              <th>{t("registry.address")}</th>
              <th>{t("registry.status")}</th>
            </tr>
          </thead>
          <tbody>
            {instances.map((i) => (
              <tr key={i.instance_id}>
                <td>{i.service_type}</td>
                <td>{i.instance_id}</td>
                <td>{i.version}</td>
                <td>{i.address}</td>
                <td>
                  <span className={`badge ${i.healthy ? "ok" : "down"}`}>
                    {i.healthy ? t("registry.healthy") : t("registry.down")}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {!isLoading && instances.length === 0 && (
        <p className="empty-state">{t("registry.empty")}</p>
      )}
    </>
  );
}
