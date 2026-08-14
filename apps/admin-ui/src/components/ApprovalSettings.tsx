"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  listApprovalConfig,
  putApprovalConfig,
  type ApprovalActionConfig,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Generic four-eyes-principle settings page (post-roadmap phase 22 session
// 3) - `GET /approval-config` only returns action types that are already
// configured (if a row is missing, `requires_approval=false` applies
// implicitly) - there is no fixed catalog of all action types that exist in
// the system. The form below therefore allows a not-yet-configured action
// type to be created for the first time via free text, in addition to the
// toggle for each already-existing row.
export function ApprovalSettings() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [configs, setConfigs] = useState<ApprovalActionConfig[]>([]);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [togglingActionType, setTogglingActionType] = useState<string | null>(null);

  const [newActionType, setNewActionType] = useState("");
  const [newRequiresApproval, setNewRequiresApproval] = useState(true);
  const [newRequiredPermission, setNewRequiredPermission] = useState("");
  const [isCreating, setIsCreating] = useState(false);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      const loaded = await listApprovalConfig(accessToken);
      setConfigs([...loaded].sort((a, b) => a.action_type.localeCompare(b.action_type)));
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

  async function handleToggle(config: ApprovalActionConfig) {
    if (!accessToken) return;
    setError(null);
    setTogglingActionType(config.action_type);
    try {
      // `required_permission` is deliberately sent unchanged - otherwise the
      // backend overwrites it with `null`, see `putApprovalConfig`.
      await putApprovalConfig(accessToken, config.action_type, {
        requiresApproval: !config.requires_approval,
        requiredPermission: config.required_permission,
      });
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("approvalSettings.toggleError"));
    } finally {
      setTogglingActionType(null);
    }
  }

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    if (!accessToken || !newActionType.trim()) return;
    setError(null);
    setIsCreating(true);
    try {
      await putApprovalConfig(accessToken, newActionType.trim(), {
        requiresApproval: newRequiresApproval,
        requiredPermission: newRequiredPermission.trim() || null,
      });
      setNewActionType("");
      setNewRequiresApproval(true);
      setNewRequiredPermission("");
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("approvalSettings.createError"));
    } finally {
      setIsCreating(false);
    }
  }

  return (
    <div className="card">
      <p className="hint">{t("approvalSettings.hint")}</p>

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : unreachable ? (
        <p className="empty-state">{t("approvalSettings.unreachable")}</p>
      ) : (
        <>
          {configs.length === 0 ? (
            <p className="empty-state">{t("approvalSettings.empty")}</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t("approvalSettings.actionType")}</th>
                  <th>{t("approvalSettings.requiresApproval")}</th>
                  <th>{t("approvalSettings.requiredPermission")}</th>
                  <th>{t("approvalSettings.updatedAt")}</th>
                </tr>
              </thead>
              <tbody>
                {configs.map((config) => (
                  <tr key={config.action_type}>
                    <td>{config.action_type}</td>
                    <td>
                      <label className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={config.requires_approval}
                          disabled={togglingActionType !== null}
                          onChange={() => handleToggle(config)}
                        />
                        {config.requires_approval
                          ? t("approvalSettings.active")
                          : t("approvalSettings.inactive")}
                      </label>
                    </td>
                    <td>{config.required_permission ?? "—"}</td>
                    <td>{new Date(config.updated_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      <h2>{t("approvalSettings.createHeading")}</h2>
      <form
        aria-label={t("approvalSettings.createFormLabel")}
        className="form-grid"
        onSubmit={handleCreate}
      >
        <label>
          {t("approvalSettings.actionType")}
          <input
            value={newActionType}
            onChange={(e) => setNewActionType(e.target.value)}
            placeholder={t("approvalSettings.actionTypePlaceholder")}
            required
          />
        </label>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={newRequiresApproval}
            onChange={(e) => setNewRequiresApproval(e.target.checked)}
          />
          {t("approvalSettings.requiresApproval")}
        </label>
        <label>
          {t("approvalSettings.requiredPermission")}
          <input
            value={newRequiredPermission}
            onChange={(e) => setNewRequiredPermission(e.target.value)}
            placeholder={t("approvalSettings.requiredPermissionPlaceholder")}
          />
        </label>
        <button type="submit" disabled={isCreating}>
          {t("common.save")}
        </button>
      </form>
    </div>
  );
}
