import { testWithPairedInstallationCleanup as test, expect, loginViaUi } from "./fixtures";

// Full create -> show-one-time-key -> list -> delete cycle (7.2, ADR 0034) -
// the deepest testable flow in this app, since transfers can't realistically
// be exercised end-to-end without a genuine paired counterpart instance (see
// transfers.spec.ts). `pairedInstallationName` (fixtures.ts) guarantees the
// row this test creates is removed again even if an assertion fails midway.
test("creates a paired installation, shows the one-time API key, and deletes it again", async ({
  page,
  pairedInstallationName,
}) => {
  await loginViaUi(page);
  await page.getByRole("link", { name: "Gepaarte Installationen" }).click();
  await expect(page).toHaveURL(/\/paired-installations/);
  await expect(page.getByRole("heading", { name: "Gepaarte Installationen (7.2)" })).toBeVisible();

  await page.getByRole("button", { name: "Installation paaren" }).click();
  await page.locator("#display-name").fill(pairedInstallationName);
  // Doesn't need to be reachable for the creation call itself to succeed
  // (migration-service only stores it, connectivity is only exercised once
  // an actual transfer step runs against it).
  await page.locator("#base-url").fill("http://localhost:8009");
  await page.getByRole("button", { name: "Paaren", exact: true }).click();

  // The generated API key is shown only once, right after creation, never
  // retrievable again via GET (docs/services/migration-console.md "Paired
  // installations").
  await expect(page.getByText("API-Key (nur jetzt sichtbar")).toBeVisible();
  await expect(page.locator("code")).toBeVisible();

  const row = page.locator("table.data-table tbody tr", { hasText: pairedInstallationName });
  await expect(row).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await row.getByRole("button", { name: "Löschen" }).click();
  await expect(row).toHaveCount(0);
});
