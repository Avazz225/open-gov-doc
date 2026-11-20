import { expect, loginViaUi, testWithIsolatedFolder } from "./fixtures";

// Narrow widths (P83-S13, ADR 0238 Decision 5): below 768px the workspace
// shows one pane at a time. Uploading and opening a document jumps to
// Vorschau; the switcher leads back; nothing scrolls the page sideways; and
// widening the window brings the dockview layout back with the document.
testWithIsolatedFolder.use({ viewport: { width: 390, height: 844 } });

const NAVIGATE_TIMEOUT = 30_000;

async function expectNoPageOverflow(page: import("@playwright/test").Page) {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
}

testWithIsolatedFolder(
  "works in single-pane mode at 390px and restores dockview when widened",
  async ({ page, isolatedFolder }) => {
    await loginViaUi(page);
    const switcher = page.getByRole("group", { name: "Arbeitsbereich" });
    await expect(switcher.getByRole("button", { name: "Explorer" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("button", { name: "Standardanordnung" })).toHaveCount(0);
    await expectNoPageOverflow(page);

    await page.getByText(isolatedFolder.name).click({ timeout: NAVIGATE_TIMEOUT });
    const documentTitle = `e2e-narrow-${Date.now()}`;
    await page.getByRole("button", { name: "Hochladen" }).click();
    await page.getByLabel("Datei").setInputFiles({
      name: "e2e-narrow.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("Playwright narrow-width test content"),
    });
    await page.getByPlaceholder("Titel (optional)").fill(documentTitle);
    await page.locator('form button[type="submit"]').click();
    await page.getByText(documentTitle).click({ timeout: 15_000 });

    await expect(switcher.getByRole("button", { name: "Vorschau (1)" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByLabel(`Vorschau: ${documentTitle}`)).toBeVisible();
    await expectNoPageOverflow(page);

    await switcher.getByRole("button", { name: "Metadaten" }).click();
    // The metadata pane's own section (it holds more than one save button).
    await expect(page.locator('section[aria-label="Metadaten"]')).toBeVisible();

    // Widening hands back to dockview, with the document opened meanwhile as a tab.
    await page.setViewportSize({ width: 1400, height: 900 });
    await expect(page.getByRole("button", { name: "Standardanordnung" })).toBeVisible();
    await expect(page.getByRole("tab", { name: documentTitle })).toBeVisible();
  }
);
