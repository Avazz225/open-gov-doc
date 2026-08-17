import { test, expect } from "@playwright/test";
import { errorAlert, loginViaUi } from "./fixtures";

// `GET /tasks` (workflow-service) aggregates ready manual/signature tasks
// across every running instance in the shared dev stack - in practice this
// is NOT empty (other suites leave real, orphaned process instances
// behind), so this exercises the actual "Bearbeiten" edit form rather than
// only an empty-state smoke check. It deliberately stops short of
// completing a task: these rows belong to process instances this suite
// didn't create and doesn't own, so mutating/finishing one isn't this
// suite's call to make - only the client-side JSON validation path (which
// never reaches the backend) and the cancel action are exercised.
test("loads the task inbox and the edit form validates JSON without submitting", async ({
  page,
}) => {
  await loginViaUi(page);
  await expect(page.getByRole("heading", { name: "Freigabeaufgaben" })).toBeVisible();
  await expect(errorAlert(page)).toHaveCount(0);

  const table = page.locator("table.data-table");
  const hasTasks = (await table.count()) > 0;

  if (!hasTasks) {
    await expect(page.getByText("Keine offenen Aufgaben.")).toBeVisible();
    return;
  }

  const firstRow = table.locator("tbody tr").first();
  await firstRow.getByRole("button", { name: "Bearbeiten" }).click();

  const dataField = page.locator("textarea[id^='data-']");
  await expect(dataField).toBeVisible();
  await dataField.fill("{not valid json");
  await page.getByRole("button", { name: "Abschließen" }).click();
  await expect(errorAlert(page)).toContainText("Kein gültiges JSON");

  await page.getByRole("button", { name: "Abbrechen" }).click();
  await expect(dataField).toHaveCount(0);
});
