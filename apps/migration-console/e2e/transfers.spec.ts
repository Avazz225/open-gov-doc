import { test, expect } from "@playwright/test";
import { errorAlert, loginViaUi } from "./fixtures";

// Transfer creation needs a genuine paired counterpart instance to complete
// its phases (lock/copy/verify/release) - out of reach for an isolated E2E
// run against a single stack, so this stays a smoke-level flow: the console
// loads (whatever transfers are already in the shared dev stack, if any),
// and the status filter reloads cleanly for every option without erroring
// (docs/services/migration-console.md documents 5s polling on top of this).
test("loads the transfer console and the status filter reloads without error", async ({
  page,
}) => {
  await loginViaUi(page);
  await expect(page.getByRole("heading", { name: "Transfers (7.2)" })).toBeVisible();
  await expect(errorAlert(page)).toHaveCount(0);

  for (const status of ["failed", "released", ""]) {
    await page.locator("#status-filter").selectOption(status);
    await expect(errorAlert(page)).toHaveCount(0);
  }

  // Either the empty state or a populated table is a valid outcome here.
  const empty = page.getByText("Noch keine Transfers.");
  const table = page.locator("table.data-table");
  await expect(empty.or(table)).toBeVisible();
});
