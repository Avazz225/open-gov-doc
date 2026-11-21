import { test, expect, loginViaUi } from "./fixtures";

// Registry overview (/registry/) - not just a page-load smoke test: asserts
// the table is populated with real registry data from the running stack and
// that the manual refresh re-fetches it. Since P87-S3 (ADR 0241) there is
// one row per service type with its replica state; a type expands to its
// instances, each with a reachable/unreachable badge.
test("shows registered service types, expands one to its instances and can refresh", async ({
  page,
}) => {
  await loginViaUi(page);
  await page.goto("/registry/");

  const table = page.locator("table.data-table").first();
  await expect(table).toBeVisible({ timeout: 15_000 });

  const rows = table.locator(":scope > tbody > tr");
  await expect(rows.first()).toBeVisible({ timeout: 15_000 });
  expect(await rows.count()).toBeGreaterThan(0);

  // A type row's state badge is one of the five translated states.
  await expect(rows.first().locator(".badge")).toHaveText(
    /^(OK|Zu wenige|Verschwunden|Zu viele|Nicht überwacht)$/
  );

  // The registry registers itself, so its own type is always there.
  await page.getByRole("button", { name: "registry-service" }).click();
  const instances = table.locator("table.data-table tbody tr");
  await expect(instances.first().locator(".badge")).toHaveText(/^(Erreichbar|Nicht erreichbar|Im Drain)$/);

  await page.getByRole("button", { name: "Aktualisieren" }).click();
  await expect(rows.first()).toBeVisible({ timeout: 15_000 });
});
