import { test as base, expect } from "@playwright/test";

// Bootstrapped domain-admin technical account (auth-service, present in
// every dev stack) - reused across E2E runs instead of provisioning a
// throwaway user per test (same rationale as user-ui/e2e/fixtures.ts).
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

// A unique, throwaway paired-installation display name for the test to use,
// plus a forced API cleanup after the test that removes any installation
// with that name regardless of whether the UI's own delete flow ran or
// succeeded - same "never leave test data in the shared dev stack" rationale
// as user-ui's isolatedFolder fixture. The dev stack's paired-installations
// list already has hundreds of leftover rows from other test suites (see
// docs/services/migration-console.md); this suite doesn't add to that pile.
export const testWithPairedInstallationCleanup = base.extend<{
  pairedInstallationName: string;
}>({
  pairedInstallationName: async ({}, use) => {
    const name = `e2e-console-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

    await use(name);

    const { access_token: token } = await login(TEST_USERNAME);
    const listRes = await fetch(
      `${GATEWAY_BASE_URL}/api/migration-service/paired-installations`,
      { headers: { Authorization: `Bearer ${token}` } }
    );
    if (!listRes.ok) return;
    const installations = (await listRes.json()) as { id: string; display_name: string }[];
    const leftover = installations.find((i) => i.display_name === name);
    if (leftover) {
      await fetch(
        `${GATEWAY_BASE_URL}/api/migration-service/paired-installations/${leftover.id}`,
        { method: "DELETE", headers: { Authorization: `Bearer ${token}` } }
      );
    }
  },
});

export { expect };

// Next.js's own hidden route announcer (`#__next-route-announcer__`, used
// for screen-reader navigation announcements) also carries `role="alert"`
// and gets inserted into the DOM after the first client-side navigation -
// `page.getByRole("alert")` alone therefore also matches it (with empty
// text), which breaks any assertion that a page has *no* error banner.
// Scoping to the app's own `.error-text` class (used consistently for every
// real error/validation message, see login/page.tsx, TransferConsole.tsx,
// PairedInstallationList.tsx) avoids that false positive.
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
