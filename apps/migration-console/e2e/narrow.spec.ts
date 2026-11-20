import { expect, loginViaUi, testWithPairedInstallationCleanup as test } from "./fixtures";

// Narrow widths (P83-S10, ADR 0238): at phone width the page itself never
// scrolls sideways - tables and the tab strip scroll inside their own
// containers - and the header cluster is reachable through the account menu.
test.use({ viewport: { width: 390, height: 844 } });

async function expectNoPageOverflow(page: import("@playwright/test").Page) {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
}

test("has no page overflow at 390px and offers the header controls in the account menu", async ({ page }) => {
  await loginViaUi(page);
  await expect(page.getByRole("heading", { name: "Transfers" })).toBeVisible();
  await expectNoPageOverflow(page);

  await page.getByRole("link", { name: "Gepaarte Installationen" }).click();
  await expect(page.getByRole("heading", { name: "Gepaarte Installationen" })).toBeVisible();
  await expectNoPageOverflow(page);

  // The inline cluster is hidden at this width; the menu holds it instead.
  await expect(page.getByRole("button", { name: "Abmelden" })).toBeHidden();
  const menuButton = page.getByRole("button", { name: /^Konto: / });
  await menuButton.click();
  await expect(menuButton).toHaveAttribute("aria-expanded", "true");
  // Scoped to the popover: the hidden inline cluster carries the same labels.
  const panel = page.locator(`[id="${await menuButton.getAttribute("aria-controls")}"]`);
  await expect(panel.getByLabel("Sprache")).toBeVisible();
  await expect(panel.getByLabel("Design")).toBeVisible();
  await expect(panel.getByRole("button", { name: "Abmelden" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(menuButton).toHaveAttribute("aria-expanded", "false");
  await expect(menuButton).toBeFocused();
});
