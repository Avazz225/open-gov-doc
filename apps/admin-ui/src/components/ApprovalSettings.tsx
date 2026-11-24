"use client";

import { Fragment, useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  getApprovalRecipients,
  listApprovalConfig,
  listRoles,
  putApprovalConfig,
  type ApprovalActionConfig,
  type ApprovalRecipients,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { PrincipalName } from "./PrincipalName";
import { BoundedList, useListFilter } from "@/components/BoundedList";

// Generic four-eyes-principle settings page (post-roadmap phase 22 session
// 3) - `GET /approval-config` only returns action types that are already
// configured (if a row is missing, `requires_approval=false` applies
// implicitly) - there is no fixed catalog of all action types that exist in
// the system. The form below therefore allows a not-yet-configured action
// type to be created for the first time via free text, in addition to the
// toggle for each already-existing row.
function actionTypeText(config: { action_type: string }): string {
  return config.action_type;
}

export function ApprovalSettings() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [configs, setConfigs] = useState<ApprovalActionConfig[]>([]);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [togglingActionType, setTogglingActionType] = useState<string | null>(null);
  // Approver notification (P90-S3, ADR 0247): the role names for the
  // recipient choice, and the "who would be told" preview per row.
  const [roleNames, setRoleNames] = useState<string[]>([]);
  const [preview, setPreview] = useState<ApprovalRecipients | null>(null);

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
      const [loaded, roles] = await Promise.all([
        listApprovalConfig(accessToken),
        listRoles(accessToken).catch(() => []),
      ]);
      setConfigs([...loaded].sort((a, b) => a.action_type.localeCompare(b.action_type)));
      setRoleNames(roles.map((role) => role.name).sort((a, b) => a.localeCompare(b)));
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
      // Only the flag: since P90-S3 the backend keeps what isn't sent.
      await putApprovalConfig(accessToken, config.action_type, {
        requiresApproval: !config.requires_approval,
      });
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("approvalSettings.toggleError"));
    } finally {
      setTogglingActionType(null);
    }
  }

  async function handleRecipientRole(config: ApprovalActionConfig, roleName: string) {
    if (!accessToken) return;
    setError(null);
    setTogglingActionType(config.action_type);
    try {
      await putApprovalConfig(accessToken, config.action_type, {
        requiresApproval: config.requires_approval,
        recipientRole: roleName || null,
      });
      if (preview?.action_type === config.action_type) {
        setPreview(await getApprovalRecipients(accessToken, config.action_type));
      }
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("approvalSettings.toggleError"));
    } finally {
      setTogglingActionType(null);
    }
  }

  async function handlePreview(config: ApprovalActionConfig) {
    if (!accessToken) return;
    if (preview?.action_type === config.action_type) {
      setPreview(null);
      return;
    }
    setError(null);
    try {
      setPreview(await getApprovalRecipients(accessToken, config.action_type));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("approvalSettings.recipientsError"));
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

  // P91-S5: dozens of action types - filter by name, bounded height.
  const { query, setQuery, filtered } = useListFilter(configs, actionTypeText);

  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
  const fieldInput =
    "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";
  const checkbox = "h-4 w-4 rounded border-border accent-accent";

  return (
    <div className="rounded-lg border border-border p-4 mb-6">
      <p className="text-sm opacity-80">{t("approvalSettings.hint")}</p>

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : unreachable ? (
        <p className="italic opacity-70">{t("approvalSettings.unreachable")}</p>
      ) : (
        <>
          {configs.length === 0 ? (
            <p className="italic opacity-70">{t("approvalSettings.empty")}</p>
          ) : (
            <BoundedList
              label={t("approvalSettings.listLabel")}
              total={configs.length}
              shown={filtered.length}
              query={query}
              onQueryChange={setQuery}
            >
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t("approvalSettings.actionType")}</th>
                    <th>{t("approvalSettings.requiresApproval")}</th>
                    <th>{t("approvalSettings.requiredPermission")}</th>
                    <th>{t("approvalSettings.recipientRole")}</th>
                    <th>{t("approvalSettings.updatedAt")}</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((config) => (
                    <Fragment key={config.action_type}>
                    <tr>
                      <td>{config.action_type}</td>
                      <td>
                        <label className="checkbox-label">
                          <input
                            type="checkbox"
                            checked={config.requires_approval}
                            disabled={togglingActionType !== null}
                            onChange={() => handleToggle(config)}
                            className={checkbox}
                          />
                          {config.requires_approval
                            ? t("approvalSettings.active")
                            : t("approvalSettings.inactive")}
                        </label>
                      </td>
                      <td>{config.required_permission ?? "—"}</td>
                      <td>
                        <div className="flex items-center gap-2">
                          <select
                            aria-label={t("approvalSettings.recipientRoleFor", { action: config.action_type })}
                            value={config.recipient_role ?? ""}
                            disabled={togglingActionType !== null}
                            onChange={(e) => handleRecipientRole(config, e.target.value)}
                            className={`${fieldInput} w-auto max-w-[14rem]`}
                          >
                            <option value="">{t("approvalSettings.recipientRoleDefault")}</option>
                            {roleNames.map((name) => (
                              <option key={name} value={name}>
                                {name}
                              </option>
                            ))}
                          </select>
                          <button
                            type="button"
                            className={secondaryBtn}
                            aria-expanded={preview?.action_type === config.action_type}
                            onClick={() => handlePreview(config)}
                          >
                            {t("approvalSettings.showRecipients")}
                          </button>
                        </div>
                      </td>
                      <td className="whitespace-nowrap">{new Date(config.updated_at).toLocaleString()}</td>
                    </tr>
                    {preview?.action_type === config.action_type && (
                      <tr>
                        <td colSpan={5}>
                          <p className="m-0 text-sm" role="status">
                            {t(`approvalSettings.recipientSource.${preview.source}`)}
                            {" · "}
                            {preview.recipients.length === 0
                              ? t("approvalSettings.recipientsNone")
                              : t("approvalSettings.recipientsCount", { count: preview.recipients.length })}
                            {preview.truncated && ` ${t("approvalSettings.recipientsTruncated")}`}
                          </p>
                          {preview.recipients.length > 0 && (
                            <ul className="m-0 mt-1 flex list-none flex-wrap gap-2 p-0">
                              {preview.recipients.map((principal) => (
                                <li key={principal} className="badge neutral">
                                  <PrincipalName value={principal} />
                                </li>
                              ))}
                            </ul>
                          )}
                        </td>
                      </tr>
                    )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </BoundedList>
          )}
        </>
      )}

      {error && (
        <p className="text-danger" role="alert">
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
            className={fieldInput}
          />
        </label>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={newRequiresApproval}
            onChange={(e) => setNewRequiresApproval(e.target.checked)}
            className={checkbox}
          />
          {t("approvalSettings.requiresApproval")}
        </label>
        <label>
          {t("approvalSettings.requiredPermission")}
          <input
            value={newRequiredPermission}
            onChange={(e) => setNewRequiredPermission(e.target.value)}
            placeholder={t("approvalSettings.requiredPermissionPlaceholder")}
            className={fieldInput}
          />
        </label>
        <button type="submit" disabled={isCreating} className={primaryBtn}>
          {t("common.save")}
        </button>
      </form>
    </div>
  );
}
