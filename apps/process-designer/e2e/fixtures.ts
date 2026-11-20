import { test as base, expect } from "@playwright/test";

// Bootstrapped domain-admin technical account (auth-service, present in
// every dev stack) - reused across E2E runs instead of provisioning a
// throwaway user per test, same rationale as apps/user-ui/e2e/fixtures.ts.
export const TEST_USERNAME = process.env.E2E_USERNAME || "users-admin";
export const TEST_PASSWORD = process.env.E2E_PASSWORD || "users-admin";

export const GATEWAY_BASE_URL = process.env.E2E_GATEWAY_BASE_URL || "http://localhost:8009";

export async function apiLogin(username: string = TEST_USERNAME): Promise<{ access_token: string }> {
  const response = await fetch(`${GATEWAY_BASE_URL}/api/auth-service/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password: TEST_PASSWORD }),
  });
  if (!response.ok) {
    throw new Error(`API login failed for ${username}: ${response.status}`);
  }
  return (await response.json()) as { access_token: string };
}

// Best-effort cleanup for a process definition created through the UI
// during a test (see designer.spec.ts) - `DELETE
// /workflow-service/process-definitions/{id}` is a real hard delete (see
// repository.py`delete_process_definition`, not a soft-delete/tombstone),
// so this leaves no residue as long as no process instance was started
// against the test definition (never the case here - the test never
// starts a workflow instance).
export async function deleteProcessDefinitionViaApi(token: string, id: number): Promise<void> {
  await fetch(`${GATEWAY_BASE_URL}/api/workflow-service/process-definitions/${id}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
}

// `users-admin` needs the `admin.object_config` capability
// (domain-admin-config role, see docs/services/process-designer.md
// "Authorization") to save/delete process and DMN definitions. Until P83-S8
// this was a manual one-time grant per dev stack (this directory's README),
// which a stack reset silently undid - the suite then failed with nothing
// pointing at the missing role. The auto fixture below grants the role for
// the length of each test and revokes it afterwards, so the stack keeps its
// default role set. users-admin may grant roles itself (admin.user_management).
async function grantRole(token: string, permission: string): Promise<number> {
  const auth = { Authorization: `Bearer ${token}` };
  const me = (await (await fetch(`${GATEWAY_BASE_URL}/api/auth-service/me`, { headers: auth })).json()) as {
    sub: string;
  };
  const roles = (await (
    await fetch(`${GATEWAY_BASE_URL}/api/permission-service/roles`, { headers: auth })
  ).json()) as { id: number; name: string; permissions: string[] }[];
  const role = roles.find((r) => r.permissions.includes(permission));
  if (!role) throw new Error(`No role carries ${permission}`);
  const response = await fetch(`${GATEWAY_BASE_URL}/api/permission-service/role-assignments`, {
    method: "POST",
    headers: { ...auth, "Content-Type": "application/json" },
    body: JSON.stringify({
      principal_type: "user",
      principal_id: me.sub,
      role_id: role.id,
      resource_id: "root",
    }),
  });
  if (!response.ok) throw new Error(`Role grant failed: ${response.status}`);
  const body = (await response.json()) as { role_assignment: { id: number } | null };
  if (!body.role_assignment) throw new Error("Role grant went to approval instead of being applied");
  return body.role_assignment.id;
}

export const test = base.extend<{ objectConfigRole: void }>({
  objectConfigRole: [
    async ({}, use) => {
      const { access_token: token } = await apiLogin();
      const assignmentId = await grantRole(token, "admin.object_config");
      try {
        await use();
      } finally {
        await fetch(`${GATEWAY_BASE_URL}/api/permission-service/role-assignments/${assignmentId}`, {
          method: "DELETE",
          headers: { Authorization: `Bearer ${token}` },
        });
      }
    },
    { auto: true },
  ],
});
export { expect };

export async function loginViaUi(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.locator("#username").fill(TEST_USERNAME);
  await page.locator("#password").fill(TEST_PASSWORD);
  await page.getByRole("button", { name: "Anmelden" }).click();
  await expect(page).toHaveURL("/");
}
