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

// Pairing is gated behind `admin.migration_management` (role
// `domain-admin-migration`, P59-S5), which no bootstrapped account holds.
// The fixture grants that role to the test account for the duration of the
// test and revokes it afterwards (P83-S8), so the shared dev stack keeps its
// default role set. users-admin may grant roles itself (admin.user_management).
async function grantMigrationRole(token: string): Promise<number> {
  const auth = { Authorization: `Bearer ${token}` };
  const me = (await (await fetch(`${GATEWAY_BASE_URL}/api/auth-service/me`, { headers: auth })).json()) as {
    sub: string;
  };
  const roles = (await (
    await fetch(`${GATEWAY_BASE_URL}/api/permission-service/roles`, { headers: auth })
  ).json()) as { id: number; name: string }[];
  const role = roles.find((r) => r.name === "domain-admin-migration");
  if (!role) throw new Error("Role domain-admin-migration not seeded");
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
    const { access_token: token } = await login(TEST_USERNAME);
    const assignmentId = await grantMigrationRole(token);

    try {
      await use(name);
    } finally {
      await fetch(`${GATEWAY_BASE_URL}/api/permission-service/role-assignments/${assignmentId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
    }

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
// Filtering to alerts that carry text avoids that false positive (P83-S8):
// every real error/validation message has text, the announcer has none. It
// replaces a `.error-text` class scope that the Tailwind migration (P75-S4)
// removed from the app - the spec had been stale since then.
export function errorAlert(page: import("@playwright/test").Page) {
  return page.getByRole("alert").filter({ hasText: /\S/ });
}

export async function loginViaUi(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.locator("#username").fill(TEST_USERNAME);
  await page.locator("#password").fill(TEST_PASSWORD);
  await page.getByRole("button", { name: "Anmelden" }).click();
  await expect(page).toHaveURL("/");
}
