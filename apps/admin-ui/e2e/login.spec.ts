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
