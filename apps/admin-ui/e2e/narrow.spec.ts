import { test, expect } from "@playwright/test";
import { loginViaUi } from "./fixtures";

// Narrow widths (P83-S12, ADR 0238): at phone width no page scrolls
// sideways, the sidebar is a modal drawer behind the menu button, and the
// header cluster lives in the account menu.
test.use({ viewport: { width: 390, height: 844 } });

async function expectNoPageOverflow(page: import("@playwright/test").Page) {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
}

test("navigates through the drawer without page overflow and offers the account menu", async ({ page }) => {
  await loginViaUi(page);
  await expectNoPageOverflow(page);

  // The sidebar column is hidden; the drawer opens as a dialog with focus inside.
  const menuButton = page.getByRole("button", { name: "Navigation öffnen" });
  await menuButton.click();
  const drawer = page.getByRole("dialog", { name: "Admin-Bereiche" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("button", { name: "Navigation schließen" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(menuButton).toBeFocused();

  // Navigating from the drawer closes it; the wide-table pages don't overflow.
  for (const [link, path] of [
    ["Nutzende & Rollen", /\/users\/?$/],
    ["Registry", /\/registry\/?$/],
    ["Standardberichte", /\/reports\/?$/],
  ] as const) {
    await menuButton.click();
    await page.getByRole("dialog").getByRole("link", { name: link }).click();
    await expect(page).toHaveURL(path);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.waitForLoadState("networkidle");
    await expectNoPageOverflow(page);
  }

  const accountButton = page.getByRole("button", { name: /^Konto: / });
  await accountButton.click();
  const panel = page.locator(`[id="${await accountButton.getAttribute("aria-controls")}"]`);
  await expect(panel.getByRole("button", { name: "Abmelden" })).toBeVisible();
});
