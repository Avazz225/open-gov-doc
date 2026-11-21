import { test, expect, loginViaUi } from "./fixtures";

// Case destruction (P89-S2, ADR 0235) against the running stack: the
// destruction schedule round-trips through the case archival settings, the
// destruction runs have their own section, and the deletion register
// includes case binders. Read-mostly: the schedule is put back in `finally`.
// Runs and register entries exist only once the pipeline destroyed a case,
// so their content isn't asserted here (the component tests cover it).
test("edits the destruction schedule and shows the destruction runs", async ({ page }) => {
  await loginViaUi(page);
  await page.goto("/archival-transfers/");

  const days = page.getByLabel("Vernichtung nach Aussonderung (Tage)");
  await expect(days).toBeVisible({ timeout: 15_000 });
  const before = await days.inputValue();
  const save = page.getByRole("form", { name: "Aussonderungs-Einstellungen für Umlaufmappen" })
    .getByRole("button", { name: "Speichern" });
  try {
    await days.fill("3650");
    await save.click();
    await expect(page.getByText("Gespeichert.")).toBeVisible();
    await page.reload();
    await expect(page.getByLabel("Vernichtung nach Aussonderung (Tage)")).toHaveValue("3650", {
      timeout: 15_000,
    });
  } finally {
    await page.getByLabel("Vernichtung nach Aussonderung (Tage)").fill(before);
    await save.click();
    await expect(page.getByText("Gespeichert.")).toBeVisible();
  }

  const section = page.getByRole("region", { name: "Vernichtung von Umlaufmappen" });
  await expect(section).toBeVisible();
  await expect(section.getByLabel("Nach Status filtern")).toBeVisible();
  if (process.env.E2E_SCREENSHOT_DIR) {
    await section.screenshot({ path: `${process.env.E2E_SCREENSHOT_DIR}/case-destructions.png` });
  }

  await page.goto("/deletion-register/");
  await expect(page.getByText(/Dokumente, Ordner und Umlaufmappen/)).toBeVisible({ timeout: 15_000 });
  if (process.env.E2E_SCREENSHOT_DIR) {
    await page.screenshot({ path: `${process.env.E2E_SCREENSHOT_DIR}/deletion-register.png`, fullPage: true });
  }
});
