import { test, expect } from "@playwright/test";
import { TEST_USERNAME, TEST_PASSWORD, loginViaUi } from "./fixtures";

test("logs in with valid credentials and reaches the admin dashboard", async ({ page }) => {
  await loginViaUi(page);
  const nav = page.getByRole("navigation", { name: "Admin-Bereiche" });
  await expect(nav).toBeVisible();
  // Scoped to the sidebar nav specifically - the dashboard's own widget
  // cards (P69-S2) repeat the same link text in their body, so the
  // unscoped locator matches two elements and violates strict mode.
  await expect(nav.getByRole("link", { name: "Nutzende & Rollen" })).toBeVisible();
});

test("shows an error and stays on the login page for wrong credentials", async ({ page }) => {
  await page.goto("/login");
  await page.locator("#username").fill(TEST_USERNAME);
  await page.locator("#password").fill(`not-${TEST_PASSWORD}`);
  await page.getByRole("button", { name: "Anmelden" }).click();

  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
});

// P86-S1 (ADR 0238 Decision 2 as amended): at desktop width too, the
// settings and log out sit behind the user name in the account menu - the
// header shows no inline log-out button any more.
test("logs out through the account menu", async ({ page }) => {
  await loginViaUi(page);
  await expect(page.getByRole("button", { name: "Abmelden" })).toHaveCount(0);
  const menuButton = page.getByRole("button", { name: `Konto: ${TEST_USERNAME}` });
  await expect(menuButton).toContainText(TEST_USERNAME);
  await menuButton.click();
  const panel = page.locator(`[id="${await menuButton.getAttribute("aria-controls")}"]`);
  await expect(panel.getByLabel("Sprache")).toBeVisible();
  await expect(panel.getByLabel("Design")).toBeVisible();
  await panel.getByRole("button", { name: "Abmelden" }).click();
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByRole("button", { name: "Anmelden" })).toBeVisible();
});
