import { test, expect } from "@playwright/test";

// Bootstrapped domain-admin technical account (auth-service, present in
// every dev stack) - reused across E2E runs instead of provisioning a
// throwaway user per test (same rationale as user-ui/e2e/fixtures.ts).
// `users-admin` carries the `domain-admin-users` realm role
// (-> admin.user_management), which is what gates the /users/ page and its
// underlying auth-service endpoints - no additional grant needed for the
// flows covered here (verified directly against the running stack: object
// type create/delete and user create/delete both succeed with this
// account's existing permissions).
export const TEST_USERNAME = process.env.E2E_USERNAME || "users-admin";
export const TEST_PASSWORD = process.env.E2E_PASSWORD || "users-admin";

export const GATEWAY_BASE_URL = process.env.E2E_GATEWAY_BASE_URL || "http://localhost:8009";

export { test, expect };

export async function loginViaUi(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.locator("#username").fill(TEST_USERNAME);
  await page.locator("#password").fill(TEST_PASSWORD);
  await page.getByRole("button", { name: "Anmelden" }).click();
  await expect(page).toHaveURL("/");
}
