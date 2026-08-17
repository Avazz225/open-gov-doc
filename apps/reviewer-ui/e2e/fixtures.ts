import { expect } from "@playwright/test";

// Bootstrapped domain-admin technical account (auth-service, present in
// every dev stack) - reused across E2E runs instead of provisioning a
// throwaway user per test (same rationale as user-ui/e2e/fixtures.ts).
// Per docs/services/reviewer-ui.md "Authorization", this app has no
// capability-gated actions - RequireAuth only checks for a valid session,
// so no special role/permission grant is needed for this account here.
export const TEST_USERNAME = process.env.E2E_USERNAME || "users-admin";
export const TEST_PASSWORD = process.env.E2E_PASSWORD || "users-admin";

export const GATEWAY_BASE_URL = process.env.E2E_GATEWAY_BASE_URL || "http://localhost:8009";

export { expect };

// Next.js's own hidden route announcer (`#__next-route-announcer__`, used
// for screen-reader navigation announcements) also carries `role="alert"`
// and gets inserted into the DOM after the first client-side navigation -
// `page.getByRole("alert")` alone therefore also matches it (with empty
// text), which breaks any assertion that a page has *no* error banner.
// Scoping to the app's own `.error-text` class (used consistently for every
// real error/validation message, see login/page.tsx, TaskList.tsx,
// ApprovalList.tsx) avoids that false positive.
export function errorAlert(page: import("@playwright/test").Page) {
  return page.locator('[role="alert"].error-text');
}

export async function loginViaUi(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.locator("#username").fill(TEST_USERNAME);
  await page.locator("#password").fill(TEST_PASSWORD);
  await page.getByRole("button", { name: "Anmelden" }).click();
  await expect(page).toHaveURL("/");
}
