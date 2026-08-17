import { test as base, expect } from "@playwright/test";

// Bootstrapped domain-admin technical account (auth-service, present in
// every dev stack) - reused across E2E runs instead of provisioning a
// throwaway user per test, same rationale as apps/user-ui/e2e/fixtures.ts.
export const TEST_USERNAME = process.env.E2E_USERNAME || "users-admin";
export const TEST_PASSWORD = process.env.E2E_PASSWORD || "users-admin";

export const GATEWAY_BASE_URL = process.env.E2E_GATEWAY_BASE_URL || "http://localhost:8009";

// `users-admin` needs the `admin.object_config` capability
// (domain-admin-config role, see docs/services/process-designer.md
// "Authorization") to save/delete process definitions - granted once per
// dev stack the same way apps/user-ui/e2e/README.md documents its own
// one-time grant. Reused here instead of created per test run (see this
// directory's README).
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

export const test = base;
export { expect };

export async function loginViaUi(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.locator("#username").fill(TEST_USERNAME);
  await page.locator("#password").fill(TEST_PASSWORD);
  await page.getByRole("button", { name: "Anmelden" }).click();
  await expect(page).toHaveURL("/");
}
