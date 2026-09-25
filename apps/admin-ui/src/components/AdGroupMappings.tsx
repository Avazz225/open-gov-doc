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
// `pending_approval` handling (every mutating call here can optionally be
// four-eyes-gated per installation - the default-role setting joined the
// other four in Phase 53 Session 1, ADR 0171, reversing ADR 0153's own
// deliberate scope cut on explicit request).
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
  const [defaultRolePending, setDefaultRolePending] = useState(false);

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
    setDefaultRolePending(false);
    try {
      const result = await setAdGroupMappingDefaultRole(accessToken, defaultRoleSelection || null);
      if (result.status === "pending_approval") {
        // Four-eyes principle active - not yet applied, so the previous
        // config stays displayed (it would be unchanged anyway).
        setDefaultRolePending(true);
      } else if (result.config) {
        setDefaultRole(result.config);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("adGroupMappings.defaultRoleSaveError"));
    } finally {
      setDefaultRoleSaving(false);
    }
  }

  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50";
  const fieldInput =
    "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";

  return (
    <>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      <section className="rounded-lg border border-border p-4 mb-6">
        <h2>{t("adGroupMappings.mappingsSectionTitle")}</h2>
        <p className="text-sm opacity-80">{t("adGroupMappings.mappingsHint")}</p>
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
              className={fieldInput}
            />
          </label>
          <label>
            {t("adGroupMappings.role")}
            <select
              value={newMapping.roleName}
              onChange={(e) => setNewMapping({ ...newMapping, roleName: e.target.value })}
              required
              className={fieldInput}
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
          <button type="submit" className={primaryBtn}>
            {t("common.create")}
          </button>
        </form>
        {mappingPending && <p className="text-sm opacity-80">{t("adGroupMappings.pendingApproval")}</p>}

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
                  <button type="button" onClick={() => handleDeleteMapping(m.id)} className={secondaryBtn}>
                    {t("common.delete")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {mappings.length === 0 && <p className="italic opacity-70">{t("adGroupMappings.mappingsEmpty")}</p>}
      </section>

      <section className="rounded-lg border border-border p-4 mb-6">
        <h2>{t("adGroupMappings.compositeRulesSectionTitle")}</h2>
        <p className="text-sm opacity-80">{t("adGroupMappings.compositeRulesHint")}</p>
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
              className={fieldInput}
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
              className={fieldInput}
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
          <button type="submit" className={primaryBtn}>
            {t("common.create")}
          </button>
        </form>
        {compositeRulePending && <p className="text-sm opacity-80">{t("adGroupMappings.pendingApproval")}</p>}

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
                  <button
                    type="button"
                    onClick={() => handleDeleteCompositeRule(rule.id)}
                    className={secondaryBtn}
                  >
                    {t("common.delete")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {compositeRules.length === 0 && (
          <p className="italic opacity-70">{t("adGroupMappings.compositeRulesEmpty")}</p>
        )}
      </section>

      <section className="rounded-lg border border-border p-4 mb-6">
        <h2>{t("adGroupMappings.defaultRoleSectionTitle")}</h2>
        <p className="text-sm opacity-80">{t("adGroupMappings.defaultRoleHint")}</p>
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
              className={fieldInput}
            >
              <option value="">{t("adGroupMappings.defaultRoleNone")}</option>
              {roles.map((r) => (
                <option key={r.id} value={r.name}>
                  {r.name}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" disabled={defaultRoleSaving} className={primaryBtn}>
            {t("common.save")}
          </button>
        </form>
        {defaultRolePending && <p className="text-sm opacity-80">{t("adGroupMappings.pendingApproval")}</p>}
        {defaultRole?.updated_by && (
          <p className="text-sm opacity-80">
            {t("adGroupMappings.defaultRoleLastChangedBy", { username: defaultRole.updated_by })}
          </p>
        )}
      </section>
    </>
  );
}
