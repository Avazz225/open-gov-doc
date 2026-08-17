import { test, expect } from "@playwright/test";
import { TEST_USERNAME, TEST_PASSWORD, loginViaUi } from "./fixtures";

test("logs in with valid credentials and reaches the document workspace", async ({ page }) => {
  await loginViaUi(page);
  await expect(page.locator("section.explorer-pane")).toBeVisible();
});

test("shows an error and stays on the login page for wrong credentials", async ({ page }) => {
  await page.goto("/login");
  await page.locator("#username").fill(TEST_USERNAME);
  await page.locator("#password").fill(`not-${TEST_PASSWORD}`);
  await page.getByRole("button", { name: "Anmelden" }).click();

  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
});
