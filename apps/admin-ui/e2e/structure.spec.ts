import { expect, loginViaUi, test } from "./fixtures";

// P91-S3 (ADR 0255): the "Ablagestruktur" page against the running stack -
// mode and overview, both conformity reports, and a template built in the
// editor and deleted again. The installation's structure config is not
// changed here (user-ui's structure spec does that and restores it).
const SHOTS = process.env.E2E_SCREENSHOT_DIR;

test("shows mode, overview, reports and manages a template", async ({ page }) => {
  await loginViaUi(page, { username: "config-admin", password: "config-admin" });
  await page.goto("/structure/");

  await expect(page.getByRole("heading", { name: "Durchsetzung und oberste Ebene" })).toBeVisible();
  await expect(page.getByRole("radio", { name: /^Aus/ })).toBeChecked();
  const folders = page.getByRole("region", { name: "Ordner" });
  await expect(folders.getByRole("status")).toContainText("geprüft", { timeout: 30_000 });
  const documents = page.getByRole("region", { name: "Dokumente" });
  await expect(documents.getByRole("status")).toContainText("geprüft", { timeout: 30_000 });

  const name = `E2E-Vorlage-${Date.now()}`;
  await page.getByRole("button", { name: "Neue Vorlage" }).click();
  const editor = page.getByRole("form", { name: "Vorlage bearbeiten" });
  await editor.getByLabel("Name der Vorlage").fill(name);
  const root = editor.getByRole("listitem", { name: "Oberster Ordner" });
  await root.getByLabel("Ordnername").first().fill("Projekt");
  await root.getByRole("button", { name: "Unterordner hinzufügen" }).first().click();
  await editor.getByRole("listitem", { name: "Ordner 1" }).getByLabel("Ordnername").fill("Korrespondenz");
  for (const [width, height] of [
    [1400, 900],
    [390, 844],
  ] as const) {
    await page.setViewportSize({ width, height });
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/structure-page-${width}.png`, fullPage: true });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  }
  await page.setViewportSize({ width: 1400, height: 900 });
  await editor.getByRole("button", { name: "Speichern" }).click();
  await expect(page.getByRole("status").filter({ hasText: `Vorlage „${name}“ gespeichert.` })).toBeVisible();

  await page.getByRole("button", { name: `Vorlage „${name}“ löschen` }).click();
  await expect(page.getByRole("button", { name: `Vorlage „${name}“ löschen` })).toHaveCount(0);
});
