"use client";

import { useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  createManagedInstallation,
  deleteManagedInstallation,
  getManagedInstallationsStatus,
  listManagedInstallations,
  pushInstallationLicense,
  type InstallationStatus,
  type ManagedInstallation,
} from "@/lib/api";

// Fleet Management (P67-S1) - wires up fleet-management-service, which had
// fully existed since Phase 54 Session 1 with no admin-ui view at all. Every
// endpoint here (including the plain list) is gated by the operator secret,
// unlike `ProcessingFailuresView`'s `HandoverFailuresSection` precedent
// (federation-hub-service leaves reads ungated) - so this whole section,
// not just individual actions, only loads once a key is supplied. Kept only
// in component state (never persisted), same reasoning as that precedent.
export function FleetManagementView() {
  const { t } = useI18n();
  const [operatorKey, setOperatorKey] = useState("");
  const [installations, setInstallations] = useState<ManagedInstallation[] | null>(null);
  const [statuses, setStatuses] = useState<Record<string, InstallationStatus>>({});
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newlyCreatedKey, setNewlyCreatedKey] = useState<{ id: string; key: string } | null>(null);

  const [displayName, setDisplayName] = useState("");
  const [gatewayBaseUrl, setGatewayBaseUrl] = useState("");
  const [isRegistering, setIsRegistering] = useState(false);

  const [licenseTokenById, setLicenseTokenById] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState<string | null>(null);

  async function load() {
    setIsLoading(true);
    setError(null);
    try {
      const list = await listManagedInstallations(operatorKey);
      setInstallations(list);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("fleetManagement.unreachable"));
      setInstallations(null);
    } finally {
      setIsLoading(false);
    }
  }

  async function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setIsRegistering(true);
    try {
      const created = await createManagedInstallation(
        { display_name: displayName, gateway_base_url: gatewayBaseUrl },
        operatorKey
      );
      // The plaintext key is returned exactly once - shown until the admin
      // dismisses it, never re-fetchable afterward via any GET.
      setNewlyCreatedKey({ id: created.id, key: created.fleet_agent_api_key });
      setDisplayName("");
      setGatewayBaseUrl("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("fleetManagement.registerError"));
    } finally {
      setIsRegistering(false);
    }
  }

  async function handleDelete(id: string) {
    setError(null);
    setBusyId(id);
    try {
      await deleteManagedInstallation(id, operatorKey);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("fleetManagement.deleteError"));
    } finally {
      setBusyId(null);
    }
  }

  async function handleCheckStatus() {
    setError(null);
    setIsLoading(true);
    try {
      const list = await getManagedInstallationsStatus(operatorKey);
      const byId: Record<string, InstallationStatus> = {};
      for (const s of list) byId[s.id] = s;
      setStatuses(byId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("fleetManagement.unreachable"));
    } finally {
      setIsLoading(false);
    }
  }

  async function handlePushLicense(id: string) {
    const token = licenseTokenById[id];
    if (!token) return;
    setError(null);
    setBusyId(id);
    try {
      await pushInstallationLicense(id, token, operatorKey);
      setLicenseTokenById((prev) => ({ ...prev, [id]: "" }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("fleetManagement.licenseError"));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="rounded-lg border border-border p-4 mb-6">
      <h2>{t("fleetManagement.heading")}</h2>
      <p className="text-sm opacity-80">{t("fleetManagement.hint")}</p>

      <label>
        {t("fleetManagement.operatorKey")}
        <input
          type="password"
          value={operatorKey}
          onChange={(e) => setOperatorKey(e.target.value)}
          placeholder={t("fleetManagement.operatorKeyPlaceholder")}
          autoComplete="off"
        />
      </label>
      <button type="button" onClick={load} disabled={isLoading || operatorKey.length === 0}>
        {isLoading ? t("common.loading") : t("fleetManagement.load")}
      </button>

      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      {newlyCreatedKey && (
        <div className="text-sm opacity-80" role="alert">
          <p>{t("fleetManagement.newKeyWarning")}</p>
          <code>{newlyCreatedKey.key}</code>
          <button type="button" onClick={() => setNewlyCreatedKey(null)}>
            {t("fleetManagement.dismiss")}
          </button>
        </div>
      )}

      {installations !== null && (
        <>
          <form onSubmit={handleRegister}>
            <h3>{t("fleetManagement.registerHeading")}</h3>
            <label>
              {t("fleetManagement.displayName")}
              <input
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                required
              />
            </label>
            <label>
              {t("fleetManagement.gatewayBaseUrl")}
              <input
                type="url"
                value={gatewayBaseUrl}
                onChange={(e) => setGatewayBaseUrl(e.target.value)}
                required
              />
            </label>
            <button type="submit" disabled={isRegistering}>
              {isRegistering ? t("common.loading") : t("fleetManagement.register")}
            </button>
          </form>

          <button type="button" onClick={handleCheckStatus} disabled={isLoading}>
            {t("fleetManagement.checkStatus")}
          </button>

          {installations.length === 0 ? (
            <p className="italic opacity-70">{t("fleetManagement.empty")}</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t("fleetManagement.displayName")}</th>
                  <th>{t("fleetManagement.gatewayBaseUrl")}</th>
                  <th>{t("fleetManagement.status")}</th>
                  <th>{t("fleetManagement.license")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {installations.map((inst) => {
                  const status = statuses[inst.id];
                  return (
                    <tr key={inst.id}>
                      <td>{inst.display_name}</td>
                      <td>{inst.gateway_base_url}</td>
                      <td>
                        {status
                          ? status.reachable
                            ? t("fleetManagement.statusReachable")
                            : t("fleetManagement.statusUnreachable")
                          : t("fleetManagement.statusUnknown")}
                      </td>
                      <td>
                        <input
                          value={licenseTokenById[inst.id] ?? ""}
                          onChange={(e) =>
                            setLicenseTokenById((prev) => ({
                              ...prev,
                              [inst.id]: e.target.value,
                            }))
                          }
                          placeholder={t("fleetManagement.licenseTokenPlaceholder")}
                        />
                        <button
                          type="button"
                          onClick={() => handlePushLicense(inst.id)}
                          disabled={busyId !== null || !licenseTokenById[inst.id]}
                        >
                          {t("fleetManagement.pushLicense")}
                        </button>
                      </td>
                      <td>
                        <button
                          type="button"
                          onClick={() => handleDelete(inst.id)}
                          disabled={busyId !== null}
                        >
                          {busyId === inst.id ? t("common.loading") : t("fleetManagement.delete")}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  );
}
