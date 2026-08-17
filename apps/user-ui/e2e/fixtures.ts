import { test as base, expect } from "@playwright/test";

// Bootstrapped domain-admin technical account (auth-service, present in
// every dev stack) - reused across E2E runs instead of provisioning a
// throwaway user per test (unlike loadtest/k6/scenario.js, which needs
// hundreds of concurrent, rate-limit-isolated identities; a handful of
// sequential UI flow tests don't).
export const TEST_USERNAME = process.env.E2E_USERNAME || "users-admin";
export const TEST_PASSWORD = process.env.E2E_PASSWORD || "users-admin";

export const GATEWAY_BASE_URL = process.env.E2E_GATEWAY_BASE_URL || "http://localhost:8009";

async function login(username: string) {
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

// A unique, isolated root folder per test - every flow that creates data
// works inside it, so tests never depend on (or pollute) whatever else is
// already in the shared dev stack's root folder. Torn down again in an
// automatic fixture teardown via a single cascading trash call (same
// pattern/rationale as loadtest/k6/scenario.js's teardown()).
export const testWithIsolatedFolder = base.extend<{
  isolatedFolder: { id: string; name: string; token: string };
}>({
  isolatedFolder: async ({}, use) => {
    const { access_token: token } = await login(TEST_USERNAME);
    const name = `e2e-user-ui-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const createRes = await fetch(`${GATEWAY_BASE_URL}/api/folder-service/folders`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ name, parent_id: "root", created_by: TEST_USERNAME }),
    });
    if (!createRes.ok) {
      throw new Error(`Failed to create isolated test folder: ${createRes.status}`);
    }
    const folder = (await createRes.json()) as { id: string };

    await use({ id: folder.id, name, token });

    await fetch(`${GATEWAY_BASE_URL}/api/folder-service/folders/${folder.id}/trash`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ deleted_by: TEST_USERNAME }),
    });
  },
});

export { expect };

export async function loginViaUi(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.locator("#username").fill(TEST_USERNAME);
  await page.locator("#password").fill(TEST_PASSWORD);
  await page.getByRole("button", { name: "Anmelden" }).click();
  await expect(page).toHaveURL("/");
}
