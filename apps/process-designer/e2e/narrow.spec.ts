import { expect, loginViaUi, test } from "./fixtures";

// Narrow widths (P83-S10, ADR 0238): no page overflow at phone width, the
// header cluster lives in the account menu, and the designer's properties
// panel is a toggled sheet instead of a fixed 300px column.
test.use({ viewport: { width: 390, height: 844 } });

async function expectNoPageOverflow(page: import("@playwright/test").Page) {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
}

test("has no page overflow at 390px; account menu and properties toggle work", async ({ page }) => {
  await loginViaUi(page);
  await expect(page.getByRole("heading", { name: "Prozessdefinitionen" })).toBeVisible();
  await expectNoPageOverflow(page);

  await page.getByRole("button", { name: "Entscheidungstabellen (DMN)" }).click();
  await expect(page.getByRole("heading", { name: "DMN-Entscheidungstabellen" })).toBeVisible();
  await expectNoPageOverflow(page);

  const menuButton = page.getByRole("button", { name: /^Konto: / });
  await menuButton.click();
  await expect(page.getByRole("button", { name: "Abmelden" })).toBeVisible();
  await page.keyboard.press("Escape");

  await page.goto("/designer/");
  await expect(page.locator('.djs-shape[data-element-id="StartEvent_1"]')).toBeVisible();
  await expectNoPageOverflow(page);
  // bpmn-js-properties-panel renders its header into the panel; hidden until toggled.
  const panelHeader = page.locator(".bio-properties-panel-header");
  await expect(panelHeader).toBeHidden();
  await page.getByRole("button", { name: "Eigenschaften anzeigen" }).click();
  await expect(panelHeader).toBeVisible();
  await page.getByRole("button", { name: "Eigenschaften ausblenden" }).click();
  await expect(panelHeader).toBeHidden();
});
