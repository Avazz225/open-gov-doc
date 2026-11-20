import { test, expect } from "@playwright/test";
import { loginViaUi } from "./fixtures";

// Narrow widths (P83-S11, ADR 0238): at phone width no page scrolls
// sideways, the header cluster lives in the account menu, and the task list
// stacks its rows into cards (the column header is visually hidden).
test.use({ viewport: { width: 390, height: 844 } });

async function expectNoPageOverflow(page: import("@playwright/test").Page) {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
}

test("has no page overflow at 390px, stacks task rows and offers the account menu", async ({ page }) => {
  await loginViaUi(page);
  await expect(page.getByRole("heading", { name: "Freigabeaufgaben" })).toBeVisible();
  await expectNoPageOverflow(page);

  // Stacked rows: when the list has tasks, the header row is visually hidden
  // (1px) and each row is a block-level card.
  const table = page.locator("table.dms-stack-table");
  if (await table.count()) {
    const display = await table.locator("tbody tr").first().evaluate((row) => getComputedStyle(row).display);
    expect(display).toBe("block");
    const headerBox = await table.locator("thead").boundingBox();
    expect(headerBox?.width ?? 0).toBeLessThanOrEqual(1);
  }

  for (const tab of ["Freigaben", "Team", "Vorgänge"]) {
    await page.getByRole("link", { name: tab }).click();
    await page.waitForLoadState("networkidle");
    await expectNoPageOverflow(page);
  }

  await expect(page.getByRole("button", { name: "Abmelden" })).toBeHidden();
  const menuButton = page.getByRole("button", { name: /^Konto: / });
  await menuButton.click();
  const panel = page.locator(`[id="${await menuButton.getAttribute("aria-controls")}"]`);
  await expect(panel.getByRole("button", { name: "Abmelden" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(menuButton).toBeFocused();
});
