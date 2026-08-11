"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  type KeycloakUser,
  type Role,
  type RoleAssignment,
  createRole,
  createRoleAssignment,
  createUser,
  deleteRoleAssignment,
  deleteUser,
  listRoleAssignments,
  listRoles,
  listUsers,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const ROOT_RESOURCE_ID = "root";

export function UserManagement() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [users, setUsers] = useState<KeycloakUser[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [assignments, setAssignments] = useState<RoleAssignment[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [assignmentPending, setAssignmentPending] = useState(false);

  const [newUser, setNewUser] = useState({
    username: "",
    email: "",
    password: "",
    firstName: "",
    lastName: "",
  });
  const [newRole, setNewRole] = useState({ name: "", description: "", permissions: "" });
  const [newAssignment, setNewAssignment] = useState({
    principalId: "",
    roleId: "",
    resourceId: ROOT_RESOURCE_ID,
  });

  const reload = useCallback(async () => {
    if (!accessToken) return;
    try {
      const [u, r, a] = await Promise.all([
        listUsers(accessToken),
        listRoles(accessToken),
        listRoleAssignments(accessToken),
      ]);
      setUsers(u);
      setRoles(r);
      setAssignments(a);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    }
  }, [accessToken, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  function roleName(roleId: number): string {
    return roles.find((r) => r.id === roleId)?.name ?? `#${roleId}`;
  }

  async function handleCreateUser(event: FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    try {
      await createUser(accessToken, newUser);
      setNewUser({ username: "", email: "", password: "", firstName: "", lastName: "" });
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("users.createError"));
    }
  }

  async function handleDeleteUser(userId: string) {
    if (!accessToken) return;
    try {
      await deleteUser(accessToken, userId);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.deleteError"));
    }
  }

  async function handleCreateRole(event: FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    try {
      await createRole(accessToken, {
        name: newRole.name,
        description: newRole.description,
        permissions: newRole.permissions
          .split(",")
          .map((p) => p.trim())
          .filter(Boolean),
      });
      setNewRole({ name: "", description: "", permissions: "" });
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("roles.createError"));
    }
  }

  async function handleCreateAssignment(event: FormEvent) {
    event.preventDefault();
    if (!accessToken || !newAssignment.roleId) return;
    setAssignmentPending(false);
    try {
      const result = await createRoleAssignment(accessToken, {
        principalType: "user",
        principalId: newAssignment.principalId,
        roleId: Number(newAssignment.roleId),
        resourceId: newAssignment.resourceId,
      });
      setNewAssignment({ principalId: "", roleId: "", resourceId: ROOT_RESOURCE_ID });
      if (result.status === "pending_approval") {
        // Vier-Augen aktiv (4.3/14.2) - noch nicht angelegt, daher kein
        // reload() (die Liste würde ohnehin unverändert bleiben).
        setAssignmentPending(true);
      } else {
        await reload();
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("roleAssignments.createError"));
    }
  }

  async function handleDeleteAssignment(id: number) {
    if (!accessToken) return;
    try {
      await deleteRoleAssignment(accessToken, id);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.deleteError"));
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
        <h2>{t("users.sectionTitle")}</h2>
        <form aria-label={t("users.formLabel")} className="form-grid" onSubmit={handleCreateUser}>
          <label>
            {t("users.username")}
            <input
              value={newUser.username}
              onChange={(e) => setNewUser({ ...newUser, username: e.target.value })}
              required
            />
          </label>
          <label>
            {t("users.email")}
            <input
              type="email"
              value={newUser.email}
              onChange={(e) => setNewUser({ ...newUser, email: e.target.value })}
              required
            />
          </label>
          <label>
            {t("users.password")}
            <input
              type="password"
              value={newUser.password}
              onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
              required
            />
          </label>
          <label>
            {t("users.firstName")}
            <input
              value={newUser.firstName}
              onChange={(e) => setNewUser({ ...newUser, firstName: e.target.value })}
              required
            />
          </label>
          <label>
            {t("users.lastName")}
            <input
              value={newUser.lastName}
              onChange={(e) => setNewUser({ ...newUser, lastName: e.target.value })}
              required
            />
          </label>
          <button type="submit">{t("common.create")}</button>
        </form>

        <table className="data-table">
          <thead>
            <tr>
              <th>{t("users.username")}</th>
              <th>{t("users.email")}</th>
              <th>{t("users.enabledColumn")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.username}</td>
                <td>{u.email}</td>
                <td>{u.enabled ? t("users.enabledYes") : t("users.enabledNo")}</td>
                <td>
                  <button type="button" onClick={() => handleDeleteUser(u.id)}>
                    {t("common.delete")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {users.length === 0 && <p className="empty-state">{t("users.empty")}</p>}
      </section>

      <section className="card">
        <h2>{t("roles.sectionTitle")}</h2>
        <form aria-label={t("roles.formLabel")} className="form-grid" onSubmit={handleCreateRole}>
          <label>
            {t("roles.name")}
            <input
              value={newRole.name}
              onChange={(e) => setNewRole({ ...newRole, name: e.target.value })}
              required
            />
          </label>
          <label>
            {t("roles.description")}
            <input
              value={newRole.description}
              onChange={(e) => setNewRole({ ...newRole, description: e.target.value })}
            />
          </label>
          <label>
            {t("roles.permissions")}
            <input
              value={newRole.permissions}
              onChange={(e) => setNewRole({ ...newRole, permissions: e.target.value })}
              placeholder={t("roles.permissionsPlaceholder")}
            />
          </label>
          <button type="submit">{t("common.create")}</button>
        </form>

        <table className="data-table">
          <thead>
            <tr>
              <th>{t("roles.name")}</th>
              <th>{t("roleAssignments.role")}</th>
            </tr>
          </thead>
          <tbody>
            {roles.map((r) => (
              <tr key={r.id}>
                <td>{r.name}</td>
                <td>{r.permissions.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {roles.length === 0 && <p className="empty-state">{t("roles.empty")}</p>}
      </section>

      <section className="card">
        <h2>{t("roleAssignments.sectionTitle")}</h2>
        <form
          aria-label={t("roleAssignments.formLabel")}
          className="form-grid"
          onSubmit={handleCreateAssignment}
        >
          <label>
            {t("roleAssignments.username")}
            <input
              value={newAssignment.principalId}
              onChange={(e) =>
                setNewAssignment({ ...newAssignment, principalId: e.target.value })
              }
              required
            />
          </label>
          <label>
            {t("roleAssignments.role")}
            <select
              value={newAssignment.roleId}
              onChange={(e) => setNewAssignment({ ...newAssignment, roleId: e.target.value })}
              required
            >
              <option value="" disabled>
                {t("roleAssignments.rolePlaceholder")}
              </option>
              {roles.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("roleAssignments.resource")}
            <input
              value={newAssignment.resourceId}
              onChange={(e) =>
                setNewAssignment({ ...newAssignment, resourceId: e.target.value })
              }
              required
            />
          </label>
          <button type="submit">{t("roleAssignments.submit")}</button>
        </form>
        {assignmentPending && <p className="hint">{t("roleAssignments.pendingApproval")}</p>}

        <table className="data-table">
          <thead>
            <tr>
              <th>{t("roleAssignments.userColumn")}</th>
              <th>{t("roleAssignments.roleColumn")}</th>
              <th>{t("roleAssignments.resourceColumn")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {assignments.map((a) => (
              <tr key={a.id}>
                <td>{a.principal_id}</td>
                <td>{roleName(a.role_id)}</td>
                <td>{a.resource_id}</td>
                <td>
                  <button type="button" onClick={() => handleDeleteAssignment(a.id)}>
                    {t("roleAssignments.remove")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {assignments.length === 0 && <p className="empty-state">{t("roleAssignments.empty")}</p>}
      </section>
    </>
  );
}
