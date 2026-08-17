import { test, expect } from "@playwright/test";
import { blockRealOfficeJs, installOfficeMock } from "./office-mock";

// Bootstrapped domain-admin technical account (auth-service, present in
// every dev stack) - same account/rationale as apps/user-ui/e2e/fixtures.ts.
const TEST_USERNAME = process.env.E2E_USERNAME || "users-admin";
const TEST_PASSWORD = process.env.E2E_PASSWORD || "users-admin";

// office-addin is a special case (see e2e/office-mock.ts for the full
// "why"): a Word task-pane add-in that can never reach real content in a
// plain browser without a mocked `window.Office` global installed before
// the page's own scripts run. Applied to every test in this file.
test.beforeEach(async ({ page }) => {
  await blockRealOfficeJs(page);
  await installOfficeMock(page);
});

test("resolves the Office.js gate and reaches the login form", async ({ page }) => {
  await page.goto("/");

  // Proves OfficeGate actually resolved past its loading state, not just
  // "didn't crash": RequireAuth (rendered as OfficeGate's `children`) only
  // ever reaches its login redirect once OfficeGate has stopped blocking.
  await expect(page.getByRole("heading", { name: "Anmelden" })).toBeVisible();
  await expect(page.getByText("Add-in wird initialisiert...")).toHaveCount(0);
});

test("logs in and reaches the authenticated task-pane shell", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Anmelden" })).toBeVisible();

  await page.locator("#username").fill(TEST_USERNAME);
  await page.locator("#password").fill(TEST_PASSWORD);
  await page.getByRole("button", { name: "Anmelden" }).click();

  // Real functionality beyond this point (opening/saving an actual Word
  // document via `Word.run`) depends on genuine Word document interaction
  // that can't be meaningfully mocked without a much larger investment -
  // out of scope by design. Confirming the authenticated shell itself
  // (Shell.tsx's top bar + TaskPane's initial DocumentPicker/
  // TemplatePicker view) renders correctly is the honest boundary here.
  await expect(page.getByRole("heading", { name: "Aus OG Doc öffnen" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Neu aus Vorlage" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Abmelden" })).toBeVisible();
});
