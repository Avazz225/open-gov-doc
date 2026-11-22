import { expect, GATEWAY_BASE_URL, loginViaUi, TEST_USERNAME, testWithIsolatedFolder } from "./fixtures";

// Follow-up reminders (P90-S2, ADR 0246) against the running stack: set from
// a folder's context menu with a time that has already passed (so it is due
// at once), see it on the icon rail's badge and in the "Wiedervorlagen"
// view, snooze it, complete it, and delete it. Whatever the test leaves
// behind is removed through the API in `finally`.
const NAVIGATE_TIMEOUT = 30_000;
const SHOTS = process.env.E2E_SCREENSHOT_DIR;

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

testWithIsolatedFolder("sets, finds, snoozes and completes a follow-up reminder", async ({ page, isolatedFolder }) => {
  const note = `E2E-Wiedervorlage ${Date.now()}`;
  try {
    await loginViaUi(page);
    await page.getByText(isolatedFolder.name).click({ button: "right", timeout: NAVIGATE_TIMEOUT });
    await page.getByRole("menuitem", { name: `Wiedervorlage für "${isolatedFolder.name}" …` }).click();

    const dialog = page.getByRole("dialog", { name: `Wiedervorlage: ${isolatedFolder.name}` });
    // A minute ago, local time: due at once.
    const past = new Date(Date.now() - 60_000);
    await dialog.getByLabel("Datum").fill(`${past.getFullYear()}-${pad(past.getMonth() + 1)}-${pad(past.getDate())}`);
    await dialog.getByLabel("Uhrzeit").fill(`${pad(past.getHours())}:${pad(past.getMinutes())}`);
    await dialog.getByLabel("Notiz").fill(note);
    await dialog.getByRole("button", { name: "Wiedervorlage setzen" }).click();
    await expect(dialog.getByRole("status")).toContainText("Gesetzt für");
    await expect(dialog.getByText(note)).toBeVisible();
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/reminder-dialog-1400.png` });
    await dialog.getByRole("button", { name: "Schließen" }).click();

    const railButton = page.getByRole("button", { name: /^Wiedervorlagen \(\d+ fällig\)$/ });
    await expect(railButton).toBeVisible();
    await railButton.click();

    const pane = page.getByRole("region", { name: "Wiedervorlagen" });
    const due = pane.locator("section", { has: page.getByRole("heading", { name: /^Fällig/ }) });
    const row = due.getByRole("listitem").filter({ hasText: note });
    await expect(row).toContainText(isolatedFolder.name);
    for (const [width, height] of [
      [1400, 900],
      [768, 1000],
      [390, 844],
    ] as const) {
      await page.setViewportSize({ width, height });
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/reminders-pane-${width}.png`, fullPage: true });
    }
    await page.setViewportSize({ width: 1400, height: 900 });

    // Snooze to next week: it moves from due to upcoming.
    await row.getByRole("button", { name: `„${isolatedFolder.name}“ auf nächste Woche verschieben` }).click();
    const upcoming = pane.locator("section", { has: page.getByRole("heading", { name: /^Anstehend/ }) });
    const upcomingRow = upcoming.getByRole("listitem").filter({ hasText: note });
    await expect(upcomingRow).toBeVisible();

    await upcomingRow.getByRole("button", { name: "Erledigt" }).click();
    await expect(upcomingRow).toHaveCount(0);
    await pane.getByText(/^Zuletzt erledigt/).click();
    const doneRow = pane.getByRole("listitem").filter({ hasText: note });
    await doneRow.getByRole("button", { name: "Löschen" }).click();
    await expect(pane.getByText(note)).toHaveCount(0);
  } finally {
    const headers = { Authorization: `Bearer ${isolatedFolder.token}` };
    const list = await fetch(
      `${GATEWAY_BASE_URL}/api/favorite-service/reminders?user_id=${TEST_USERNAME}&object_id=${isolatedFolder.id}&object_type=folder`,
      { headers }
    );
    for (const reminder of (await list.json()) as { id: string }[]) {
      await fetch(`${GATEWAY_BASE_URL}/api/favorite-service/reminders/${reminder.id}`, {
        method: "DELETE",
        headers,
      });
    }
  }
});
