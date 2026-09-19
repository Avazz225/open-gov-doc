"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  type AdGroupCompositeRule,
  type AdGroupMappingDefaultRole,
  type AdGroupRoleMapping,
  type Role,
  createAdGroupCompositeRule,
  createAdGroupMapping,
  deleteAdGroupCompositeRule,
  deleteAdGroupMapping,
  getAdGroupMappingDefaultRole,
  listAdGroupCompositeRules,
  listAdGroupMappings,
  listRoles,
  setAdGroupMappingDefaultRole,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// AD group -> role mapping admin UI (Phase 50 Session 5) - the backend
// (1:1 mappings since P24-S2/ADR 0093, composite AND-rules + default role
// since Post-Roadmap Phase 39 Session 3/ADR 0153) has always been API-only
// until this session. Per-row CRUD (fetch list, add-row form posting
// immediately, per-row delete calling DELETE immediately) rather than the
// batched-single-PUT style `RetentionSettings.tsx` uses - mirrors
// `UserManagement.tsx`'s "Role Assignments" section, including its
// `pending_approval` handling (all four mutating calls here can optionally
// be four-eyes-gated per installation).
export function AdGroupMappings() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [mappings, setMappings] = useState<AdGroupRoleMapping[]>([]);
  const [compositeRules, setCompositeRules] = useState<AdGroupCompositeRule[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [defaultRole, setDefaultRole] = useState<AdGroupMappingDefaultRole | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [mappingPending, setMappingPending] = useState(false);
  const [newMapping, setNewMapping] = useState({ adGroupName: "", roleName: "" });

  const [compositeRulePending, setCompositeRulePending] = useState(false);
  const [newCompositeRule, setNewCompositeRule] = useState({ roleName: "", adGroupNames: "" });

  const [defaultRoleSelection, setDefaultRoleSelection] = useState("");
  const [defaultRoleSaving, setDefaultRoleSaving] = useState(false);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    try {
      const [m, c, r, d] = await Promise.all([
        listAdGroupMappings(accessToken),
        listAdGroupCompositeRules(accessToken),
        listRoles(accessToken),
        getAdGroupMappingDefaultRole(accessToken),
      ]);
      setMappings(m);
      setCompositeRules(c);
      setRoles(r);
      setDefaultRole(d);
      setDefaultRoleSelection(d.default_role_name ?? "");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    }
  }, [accessToken, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleCreateMapping(event: FormEvent) {
    event.preventDefault();
    if (!accessToken || !newMapping.adGroupName.trim() || !newMapping.roleName) return;
    setMappingPending(false);
    try {
      const result = await createAdGroupMapping(accessToken, {
        adGroupName: newMapping.adGroupName.trim(),
        roleName: newMapping.roleName,
      });
      setNewMapping({ adGroupName: "", roleName: "" });
      if (result.status === "pending_approval") {
        // Four-eyes principle active - not yet created, so no reload() (the
        // list would remain unchanged anyway), same pattern as
        // UserManagement.tsx's role assignments.
        setMappingPending(true);
      } else {
        await reload();
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("adGroupMappings.mappingCreateError"));
    }
  }

  async function handleDeleteMapping(id: number) {
    if (!accessToken) return;
    try {
      const result = await deleteAdGroupMapping(accessToken, id);
      if (result.status === "pending_approval") {
        setMappingPending(true);
      } else {
        await reload();
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.deleteError"));
    }
  }

  function parseAdGroupNames(raw: string): string[] {
    return Array.from(new Set(raw.split(",").map((name) => name.trim()).filter(Boolean)));
  }

  async function handleCreateCompositeRule(event: FormEvent) {
    event.preventDefault();
    if (!accessToken || !newCompositeRule.roleName) return;
    const adGroupNames = parseAdGroupNames(newCompositeRule.adGroupNames);
    if (adGroupNames.length < 2) {
      setError(t("adGroupMappings.compositeRuleNeedsTwoGroups"));
      return;
    }
    setCompositeRulePending(false);
    try {
      const result = await createAdGroupCompositeRule(accessToken, {
        roleName: newCompositeRule.roleName,
        adGroupNames,
      });
      setNewCompositeRule({ roleName: "", adGroupNames: "" });
      if (result.status === "pending_approval") {
        setCompositeRulePending(true);
      } else {
        await reload();
      }
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : t("adGroupMappings.compositeRuleCreateError")
      );
    }
  }

  async function handleDeleteCompositeRule(id: number) {
    if (!accessToken) return;
    try {
      const result = await deleteAdGroupCompositeRule(accessToken, id);
      if (result.status === "pending_approval") {
        setCompositeRulePending(true);
      } else {
        await reload();
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.deleteError"));
    }
  }

  async function handleSaveDefaultRole(event: FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    setDefaultRoleSaving(true);
    try {
      const updated = await setAdGroupMappingDefaultRole(
        accessToken,
        defaultRoleSelection || null
      );
      setDefaultRole(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("adGroupMappings.defaultRoleSaveError"));
    } finally {
      setDefaultRoleSaving(false);
    }
  }

  return (
    <>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      <section className="card">
        <h2>{t("adGroupMappings.mappingsSectionTitle")}</h2>
        <p className="hint">{t("adGroupMappings.mappingsHint")}</p>
        <form
          aria-label={t("adGroupMappings.mappingsFormLabel")}
          className="form-grid"
          onSubmit={handleCreateMapping}
        >
          <label>
            {t("adGroupMappings.adGroupName")}
            <input
              value={newMapping.adGroupName}
              onChange={(e) => setNewMapping({ ...newMapping, adGroupName: e.target.value })}
              required
            />
          </label>
          <label>
            {t("adGroupMappings.role")}
            <select
              value={newMapping.roleName}
              onChange={(e) => setNewMapping({ ...newMapping, roleName: e.target.value })}
              required
            >
              <option value="" disabled>
                {t("adGroupMappings.rolePlaceholder")}
              </option>
              {roles.map((r) => (
                <option key={r.id} value={r.name}>
                  {r.name}
                </option>
              ))}
            </select>
          </label>
          <button type="submit">{t("common.create")}</button>
        </form>
        {mappingPending && <p className="hint">{t("adGroupMappings.pendingApproval")}</p>}

        <table className="data-table">
          <thead>
            <tr>
              <th>{t("adGroupMappings.adGroupName")}</th>
              <th>{t("adGroupMappings.role")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {mappings.map((m) => (
              <tr key={m.id}>
                <td>{m.ad_group_name}</td>
                <td>{m.role_name}</td>
                <td>
                  <button type="button" onClick={() => handleDeleteMapping(m.id)}>
                    {t("common.delete")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {mappings.length === 0 && <p className="empty-state">{t("adGroupMappings.mappingsEmpty")}</p>}
      </section>

      <section className="card">
        <h2>{t("adGroupMappings.compositeRulesSectionTitle")}</h2>
        <p className="hint">{t("adGroupMappings.compositeRulesHint")}</p>
        <form
          aria-label={t("adGroupMappings.compositeRulesFormLabel")}
          className="form-grid"
          onSubmit={handleCreateCompositeRule}
        >
          <label>
            {t("adGroupMappings.adGroupNames")}
            <input
              value={newCompositeRule.adGroupNames}
              onChange={(e) =>
                setNewCompositeRule({ ...newCompositeRule, adGroupNames: e.target.value })
              }
              placeholder={t("adGroupMappings.adGroupNamesPlaceholder")}
              required
            />
          </label>
          <label>
            {t("adGroupMappings.role")}
            <select
              value={newCompositeRule.roleName}
              onChange={(e) =>
                setNewCompositeRule({ ...newCompositeRule, roleName: e.target.value })
              }
              required
            >
              <option value="" disabled>
                {t("adGroupMappings.rolePlaceholder")}
              </option>
              {roles.map((r) => (
                <option key={r.id} value={r.name}>
                  {r.name}
                </option>
              ))}
            </select>
          </label>
          <button type="submit">{t("common.create")}</button>
        </form>
        {compositeRulePending && <p className="hint">{t("adGroupMappings.pendingApproval")}</p>}

        <table className="data-table">
          <thead>
            <tr>
              <th>{t("adGroupMappings.adGroupNames")}</th>
              <th>{t("adGroupMappings.role")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {compositeRules.map((rule) => (
              <tr key={rule.id}>
                <td>{rule.ad_group_names.join(", ")}</td>
                <td>{rule.role_name}</td>
                <td>
                  <button type="button" onClick={() => handleDeleteCompositeRule(rule.id)}>
                    {t("common.delete")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {compositeRules.length === 0 && (
          <p className="empty-state">{t("adGroupMappings.compositeRulesEmpty")}</p>
        )}
      </section>

      <section className="card">
        <h2>{t("adGroupMappings.defaultRoleSectionTitle")}</h2>
        <p className="hint">{t("adGroupMappings.defaultRoleHint")}</p>
        <form
          aria-label={t("adGroupMappings.defaultRoleFormLabel")}
          className="form-grid"
          onSubmit={handleSaveDefaultRole}
        >
          <label>
            {t("adGroupMappings.role")}
            <select
              value={defaultRoleSelection}
              onChange={(e) => setDefaultRoleSelection(e.target.value)}
            >
              <option value="">{t("adGroupMappings.defaultRoleNone")}</option>
              {roles.map((r) => (
                <option key={r.id} value={r.name}>
                  {r.name}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" disabled={defaultRoleSaving}>
            {t("common.save")}
          </button>
        </form>
        {defaultRole?.updated_by && (
          <p className="hint">
            {t("adGroupMappings.defaultRoleLastChangedBy", { username: defaultRole.updated_by })}
          </p>
        )}
      </section>
    </>
  );
}
