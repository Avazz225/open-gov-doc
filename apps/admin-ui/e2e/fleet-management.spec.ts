import { test, expect, loginViaUi } from "./fixtures";

// Fleet management (P67-S1) - wires up fleet-management-service, which had
// no admin-ui caller at all before this session. Every fleet-management-
// service endpoint (including the plain list) requires the operator key
// (`DMS_FLEET_OPERATOR_KEY`, TEMPORARILY set in infra/docker-compose.yml
// for this live verification only, same as P54-S1's own precedent - not a
// normal admin-JWT-gated flow like the other E2E specs in this directory).
const OPERATOR_KEY = process.env.E2E_FLEET_OPERATOR_KEY || "p67s1-live-verify-key";

test("registers a managed installation, shows the one-time key, then removes it", async ({
  page,
}) => {
  await loginViaUi(page);
  await page.goto("/fleet-management/");

  await page.getByPlaceholder("Erforderlich für jede Aktion auf dieser Seite").fill(OPERATOR_KEY);
  await page.getByRole("button", { name: "Laden" }).click();

  await expect(page.getByText("Neue Installation registrieren")).toBeVisible({ timeout: 15_000 });

  const displayName = `E2E-Installation-${Date.now()}`;
  await page.getByLabel("Anzeigename").fill(displayName);
  await page.getByLabel("Gateway-URL").fill("https://e2e-test-installation.example.com");
  await page.getByRole("button", { name: "Registrieren" }).click();

  // The plaintext fleet-agent key is shown exactly once.
  await expect(page.locator("code")).toBeVisible({ timeout: 15_000 });
  const shownKey = await page.locator("code").textContent();
  expect(shownKey).toBeTruthy();
  expect(shownKey!.length).toBeGreaterThan(10);
  await page.getByRole("button", { name: "Verstanden" }).click();

  const row = page.locator("tr", { hasText: displayName });
  await expect(row).toBeVisible({ timeout: 15_000 });
  await expect(row.getByText("https://e2e-test-installation.example.com")).toBeVisible();

  await row.getByRole("button", { name: "Entfernen" }).click();
  await expect(page.locator("tr", { hasText: displayName })).toHaveCount(0, { timeout: 15_000 });
});
