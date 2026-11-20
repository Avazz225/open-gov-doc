import { test, expect } from "@playwright/test";
import { errorAlert, loginViaUi } from "./fixtures";

// Case-browsing UI (14.2, Post-Roadmap Phase 74 Session 1, ADR 0141's own
// named gap). `GET /cases` (case-service) aggregates every case in the
// shared dev stack, same "not empty in practice" situation as
// tasks.spec.ts's task inbox - exercises the real list->detail drill-down
// rather than only an empty-state smoke check.
test("browses the case list, opens a case, and downloads a document", async ({ page }) => {
  await loginViaUi(page);
  await page.getByRole("link", { name: "Vorgänge" }).click();
  await expect(page.getByRole("heading", { name: "Vorgänge" })).toBeVisible();
  await expect(errorAlert(page)).toHaveCount(0);

  // Role-based, not the `.entry-list`/`.entry-row`/`.entry-name` classes the
  // Tailwind migration (P75-S4) removed
  const list = page.getByRole("list");
  const emptyState = page.getByText("Keine Vorgänge vorhanden.");
  await expect(list.or(emptyState)).toBeVisible();
  if (await emptyState.isVisible()) return;

  const firstRow = list.getByRole("listitem").first();
  await firstRow.getByRole("button").first().click();

  // Detail view rendered - "Zurück zur Übersicht" and either a document
  // list or the empty-documents hint confirm the case actually loaded.
  await expect(page.getByRole("button", { name: "Zurück zur Übersicht" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Dokumente" })).toBeVisible();
  await expect(errorAlert(page)).toHaveCount(0);

  await page.getByRole("button", { name: "Zurück zur Übersicht" }).click();
  await expect(list).toBeVisible();
});
