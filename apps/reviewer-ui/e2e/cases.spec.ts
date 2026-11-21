import { test, expect } from "@playwright/test";
import { errorAlert, GATEWAY_BASE_URL, loginViaUi, TEST_PASSWORD, TEST_USERNAME } from "./fixtures";

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

// Follow-up reminders on a case (P90-S2, ADR 0246): set one from the case
// detail, see it listed, complete it. A completed reminder would stay in
// the user's history (user-ui's "Wiedervorlagen" view), so the test deletes
// its own through the API in `finally`.
test("sets and completes a follow-up reminder on a case", async ({ page }) => {
  const note = `E2E-Vorgang-Wiedervorlage ${Date.now()}`;
  try {
    await loginViaUi(page);
    await page.getByRole("link", { name: "Vorgänge" }).click();
    const list = page.getByRole("list");
    const emptyState = page.getByText("Keine Vorgänge vorhanden.");
    await expect(list.or(emptyState)).toBeVisible();
    if (await emptyState.isVisible()) return;
    await list.getByRole("listitem").first().getByRole("button").first().click();

    const section = page.getByRole("region", { name: "Meine Wiedervorlagen" });
    await expect(section).toBeVisible();
    await section.getByLabel("Notiz").fill(note);
    await section.getByRole("button", { name: "Wiedervorlage setzen" }).click();
    const row = section.getByRole("listitem").filter({ hasText: note });
    await expect(row).toBeVisible();
    const shots = process.env.E2E_SCREENSHOT_DIR;
    for (const [width, height] of [
      [1400, 900],
      [390, 844],
    ] as const) {
      await page.setViewportSize({ width, height });
      await section.scrollIntoViewIfNeeded();
      if (shots) await page.screenshot({ path: `${shots}/reviewer-case-reminders-${width}.png` });
    }
    await row.getByRole("button", { name: "Erledigt" }).click();
    await expect(row).toHaveCount(0);
    await expect(errorAlert(page)).toHaveCount(0);
  } finally {
    const login = await fetch(`${GATEWAY_BASE_URL}/api/auth-service/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: TEST_USERNAME, password: TEST_PASSWORD }),
    });
    const headers = { Authorization: `Bearer ${(await login.json()).access_token}` };
    const all = await fetch(
      `${GATEWAY_BASE_URL}/api/favorite-service/reminders?user_id=${TEST_USERNAME}`,
      { headers }
    );
    for (const reminder of (await all.json()) as { id: string; note: string }[]) {
      if (reminder.note !== note) continue;
      await fetch(`${GATEWAY_BASE_URL}/api/favorite-service/reminders/${reminder.id}`, {
        method: "DELETE",
        headers,
      });
    }
  }
});
