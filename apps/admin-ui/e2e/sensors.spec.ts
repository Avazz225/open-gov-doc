import { test, expect, loginViaUi } from "./fixtures";

// Monitoring sensors (P88-S2, ADR 0243): the catalog comes from the running
// services, grouped, with the resolved state and its source. Asserts only
// what holds with or without `admin.monitoring` (editing is covered by the
// unit tests), so it never changes the stack's sensor configuration.
test("lists the running services' sensors by group and filters them", async ({ page }) => {
  await loginViaUi(page);
  await page.goto("/sensors/");

  const list = page.getByRole("region", { name: "Gruppen und Sensoren" });
  // Every service with the HTTP middleware declares http.requests.
  await expect(list.getByText("http.requests", { exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(list.locator("tbody th", { hasText: "Performance" })).toBeVisible();
  await expect(page.getByRole("radio", { name: "Alles überwachen" })).toBeVisible();
  // P88-S2c: one switch per cost level, and the level shown as dots.
  for (const level of [1, 2, 3, 4, 5]) {
    await expect(page.getByLabel(`Einstellung für Kostenstufe ${level}`)).toBeVisible();
  }
  await expect(
    list.locator("tr", { hasText: "document.count.by_type" }).locator(".badge", { hasText: "standardmäßig aus" })
  ).toBeVisible();

  await list.getByLabel("Filtern nach Name, Beschreibung oder Dienst").fill("registry.instances");
  await expect(list.getByText("registry.instances.active_total")).toBeVisible();
  await expect(list.getByText("http.requests", { exact: true })).toHaveCount(0);
});
