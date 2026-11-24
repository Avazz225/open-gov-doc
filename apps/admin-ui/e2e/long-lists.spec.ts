import { expect, loginViaUi, test } from "./fixtures";

// P91-S5 against the running stack: the long admin lists stay bounded and
// filterable. The dev stack has ~1,500 permission roles and dozens of
// approval action types, so these are real sizes, not fixtures. Read-only.
const SHOTS = process.env.E2E_SCREENSHOT_DIR;
const SIZES = [
  [1400, 900],
  [768, 1000],
  [390, 844],
] as const;

async function noHorizontalOverflow(page: import("@playwright/test").Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

test("users page: roles are bounded and filterable", async ({ page }) => {
  await loginViaUi(page);
  await page.goto("/users/");
  const roles = page.getByRole("group", { name: "Rollen – Liste" });
  await expect(roles).toBeVisible({ timeout: 30_000 });
  // Wait until the rows are in (thousands on the dev stack), then measure.
  await expect(roles.locator("tbody tr").nth(30)).toBeAttached({ timeout: 30_000 });
  // Bounded: the box is far shorter than its content.
  const sizes = await roles.evaluate((el) => ({ client: el.clientHeight, scroll: el.scrollHeight }));
  expect(sizes.client).toBeLessThan(600);
  expect(sizes.scroll).toBeGreaterThan(sizes.client);

  // The realm roles above have their own "Rollen filtern" field.
  const filter = page.locator(".bounded-list").filter({ has: roles }).getByLabel("Rollen filtern");
  await filter.fill("domain-admin-config");
  await expect(roles.getByRole("cell", { name: "domain-admin-config", exact: true })).toBeVisible();
  for (const [width, height] of SIZES) {
    await page.setViewportSize({ width, height });
    await noHorizontalOverflow(page);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/users-roles-${width}.png` });
  }
});

test("approval settings: action types are bounded and filterable", async ({ page }) => {
  await loginViaUi(page);
  await page.goto("/approval-settings/");
  const list = page.getByRole("group", { name: "Freigabepflichtige Aktionen – Liste" });
  await expect(list).toBeVisible({ timeout: 30_000 });
  await page.getByLabel("Freigabepflichtige Aktionen filtern").fill("config.import");
  await expect(list.getByRole("cell", { name: "config.import", exact: true })).toBeVisible();
  for (const [width, height] of SIZES) {
    await page.setViewportSize({ width, height });
    await noHorizontalOverflow(page);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/approval-settings-${width}.png` });
  }
});

test("object types: the list is filterable", async ({ page }) => {
  await loginViaUi(page, { username: "config-admin", password: "config-admin" });
  await page.goto("/object-types/");
  const list = page.getByRole("group", { name: "Objekttypen – Liste" });
  await expect(list).toBeVisible({ timeout: 30_000 });
  await page.getByLabel("Objekttypen filtern").fill("Aktenplan");
  await expect(list.getByRole("cell", { name: "Aktenplan", exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await noHorizontalOverflow(page);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/object-types-390.png`, fullPage: true });
});
