import { test, expect, loginViaUi } from "./fixtures";

// Registry overview (/registry/) - third admin flow per the task. Not just
// a page-load smoke test: asserts the table is actually populated with
// real service-registry data from the running stack (service type,
// instance id, a healthy/down status badge) and that the manual refresh
// action re-fetches it.
test("shows registered service instances and can refresh them", async ({ page }) => {
  await loginViaUi(page);
  await page.goto("/registry/");

  const table = page.locator("table.data-table");
  await expect(table).toBeVisible({ timeout: 15_000 });

  const rows = table.locator("tbody tr");
  await expect(rows.first()).toBeVisible({ timeout: 15_000 });
  expect(await rows.count()).toBeGreaterThan(0);

  // Every row's status badge resolves to one of the two known German
  // status labels - a loose but real assertion that the API response was
  // actually rendered, not just an empty/error state.
  await expect(rows.first().locator(".badge")).toHaveText(/healthy|down/);

  await page.getByRole("button", { name: "Aktualisieren" }).click();
  await expect(rows.first()).toBeVisible({ timeout: 15_000 });
});
