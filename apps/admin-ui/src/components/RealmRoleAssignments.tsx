"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  assignRealmRole,
  listRealmRoleMembers,
  listRealmRoles,
  unassignRealmRole,
  type KeycloakUser,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Keycloak realm-role assignment in user management (P88-S4b). Realm roles
// gate whole applications or app areas - `dms-poststelle` for the mail
// room, `dms-admin` - and until now could only be assigned in the Keycloak
// admin console. The auth-service endpoints exist since Phase 78; this is
// their UI. Role-first: a role expands to its members, a member is added by
// username (suggested from the user list) and removed with its button.
// Keycloak's own built-in roles are filtered out server-side.

const secondaryBtn =
  "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
const primaryBtn =
  "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
const fieldInput =
  "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none focus:border-accent focus:ring-2 focus:ring-accent-bg";

export function RealmRoleAssignments({ users }: { users: KeycloakUser[] }) {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [roles, setRoles] = useState<string[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [members, setMembers] = useState<Record<string, { id: string; username: string }[]>>({});
  const [newMember, setNewMember] = useState("");
  // A realm can hold many roles (packages, other applications); a filter
  // keeps the list usable.
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadRoles = useCallback(async () => {
    if (!accessToken) return;
    try {
      setRoles((await listRealmRoles(accessToken)).sort());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    }
  }, [accessToken, t]);

  useEffect(() => {
    loadRoles();
  }, [loadRoles]);

  const loadMembers = useCallback(
    async (role: string) => {
      if (!accessToken) return;
      const list = await listRealmRoleMembers(accessToken, role);
      setMembers((prev) => ({ ...prev, [role]: list.sort((a, b) => a.username.localeCompare(b.username)) }));
    },
    [accessToken]
  );

  async function toggle(role: string) {
    setError(null);
    setNewMember("");
    if (expanded === role) {
      setExpanded(null);
      return;
    }
    setExpanded(role);
    try {
      await loadMembers(role);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    }
  }

  async function run(role: string, action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await loadMembers(role);
      return true;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.actionError"));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function handleAdd(event: FormEvent, role: string) {
    event.preventDefault();
    const username = newMember.trim();
    if (!username || !accessToken) return;
    if (await run(role, () => assignRealmRole(accessToken, role, username))) setNewMember("");
  }

  return (
    <section className="rounded-lg border border-border p-4 mb-6" aria-labelledby="realm-roles-heading">
      <h2 id="realm-roles-heading">{t("realmRoles.sectionTitle")}</h2>
      <p className="text-sm opacity-80">{t("realmRoles.hint")}</p>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
      {roles.length > 10 && (
        <label className="mb-3 flex flex-col gap-1 text-sm">
          {t("realmRoles.filter")}
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className={`${fieldInput} max-w-sm`}
          />
        </label>
      )}
      {roles.length === 0 ? (
        <p className="italic opacity-70">{t("realmRoles.empty")}</p>
      ) : (
        <ul className="m-0 flex max-h-[32rem] list-none flex-col gap-2 overflow-y-auto p-0">
          {roles
            .filter((role) => role.toLowerCase().includes(filter.trim().toLowerCase()))
            .map((role) => {
            const isOpen = expanded === role;
            const roleMembers = members[role] ?? [];
            const candidates = users.filter((u) => !roleMembers.some((m) => m.username === u.username));
            return (
              <li key={role} className="rounded-md border border-border p-2">
                <button
                  type="button"
                  className="flex items-center gap-1 border-0 bg-transparent p-0 text-left font-mono text-sm text-fg"
                  aria-expanded={isOpen}
                  onClick={() => toggle(role)}
                >
                  {isOpen ? (
                    <ChevronDown size={16} aria-hidden="true" className="flex-none" />
                  ) : (
                    <ChevronRight size={16} aria-hidden="true" className="flex-none" />
                  )}
                  {role}
                  {members[role] && (
                    <span className="ml-1 font-sans text-xs opacity-70">
                      ({t("realmRoles.memberCount", { count: roleMembers.length })})
                    </span>
                  )}
                </button>
                {isOpen && (
                  <div className="mt-2 pl-5">
                    {roleMembers.length === 0 ? (
                      <p className="m-0 text-sm italic opacity-70">{t("realmRoles.noMembers")}</p>
                    ) : (
                      <ul className="m-0 flex list-none flex-wrap gap-2 p-0">
                        {roleMembers.map((member) => (
                          <li key={member.id} className="badge neutral inline-flex items-center gap-1">
                            {member.username}
                            <button
                              type="button"
                              className="border-0 bg-transparent p-0 text-inherit"
                              aria-label={t("realmRoles.remove", { user: member.username, role })}
                              disabled={busy}
                              onClick={() => run(role, () => unassignRealmRole(accessToken ?? "", role, member.username))}
                            >
                              ×
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                    <form className="mt-2 flex flex-wrap items-end gap-2" onSubmit={(e) => handleAdd(e, role)}>
                      <label className="flex flex-col gap-1 text-sm">
                        {t("realmRoles.username")}
                        <input
                          list={`realm-role-candidates-${role}`}
                          value={newMember}
                          onChange={(e) => setNewMember(e.target.value)}
                          className={fieldInput}
                        />
                      </label>
                      <datalist id={`realm-role-candidates-${role}`}>
                        {candidates.map((u) => (
                          <option key={u.id} value={u.username} />
                        ))}
                      </datalist>
                      <button type="submit" className={primaryBtn} disabled={busy || !newMember.trim()}>
                        {t("realmRoles.assign")}
                      </button>
                      <button type="button" className={secondaryBtn} onClick={() => toggle(role)}>
                        {t("realmRoles.close")}
                      </button>
                    </form>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
