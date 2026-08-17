import { test, expect } from "@playwright/test";
import { errorAlert, loginViaUi } from "./fixtures";

// Nothing currently requires approval in this dev stack (checked via
// GET /api/permission-service/approval-config - migration.transfer.start
// and every other action type present there has requires_approval: false
// or is unrelated test debris from other suites), so the inbox is
// genuinely empty. Per the task scope, deliberately not flipping
// approval-config just to populate this list on a shared stack. This stays
// a smoke-level flow: the inbox loads, and the status filter (the other
// real interactive element on this page) reloads cleanly for every option.
test("loads the approval inbox and the status filter reloads without error", async ({ page }) => {
  await loginViaUi(page);
  await page.getByRole("link", { name: "Freigaben" }).click();
  await expect(page).toHaveURL(/\/approvals/);
  await expect(page.getByRole("heading", { name: "Vier-Augen-Freigaben (4.3)" })).toBeVisible();
  await expect(errorAlert(page)).toHaveCount(0);

  for (const label of ["Alle", "Freigegeben", "Abgelehnt", "Offen"]) {
    await page.locator("#status-filter").selectOption({ label });
    await expect(errorAlert(page)).toHaveCount(0);
  }

  const empty = page.getByText("Keine Freigabeanfragen.");
  const table = page.locator("table.data-table");
  await expect(empty.or(table)).toBeVisible();
});
