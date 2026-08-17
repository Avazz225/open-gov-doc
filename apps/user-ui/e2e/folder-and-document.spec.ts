import { expect, loginViaUi, testWithIsolatedFolder } from "./fixtures";

// The dev stack's root folder accumulates a large number of pre-existing
// entries over time (documented elsewhere in this project) - navigating
// from root can be slow, hence the generous timeout below rather than a
// flaky default.
const NAVIGATE_TIMEOUT = 30_000;

testWithIsolatedFolder(
  "creates a subfolder, uploads a document into it, and finds it via search",
  async ({ page, isolatedFolder }) => {
    await loginViaUi(page);

    await page.getByText(isolatedFolder.name).click({ timeout: NAVIGATE_TIMEOUT });
    await expect(page.getByRole("navigation", { name: "Ordnerpfad" })).toContainText(
      isolatedFolder.name
    );

    // Create a subfolder.
    const subfolderName = `subfolder-${Date.now()}`;
    await page.getByRole("button", { name: "Neuer Ordner" }).click();
    await page.getByRole("textbox").first().fill(subfolderName);
    await page.getByRole("button", { name: "Anlegen" }).click();
    await expect(page.getByText(subfolderName)).toBeVisible();

    // Upload a document at this level.
    const documentTitle = `e2e-doc-${Date.now()}`;
    await page.getByRole("button", { name: "Hochladen" }).click();
    await page
      .getByLabel("Datei")
      .setInputFiles({
        name: "e2e-test.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("Playwright E2E test content"),
      });
    await page.getByPlaceholder("Titel (optional)").fill(documentTitle);
    await page.locator('form button[type="submit"]').click();

    await expect(page.getByText(documentTitle)).toBeVisible({ timeout: 15_000 });

    // Search finds the uploaded document. search-service indexing can lag
    // slightly behind the upload, so the whole submit+assert is retried
    // instead of asserting once immediately after a single submit.
    await page.getByTitle("Suche").click();
    const searchResults = page.locator("section.search-pane");
    await page.getByPlaceholder("Suchbegriff eingeben...").fill(documentTitle);
    await expect(async () => {
      await page.getByRole("button", { name: "Suchen" }).click();
      await expect(searchResults.getByText(documentTitle).first()).toBeVisible({ timeout: 2_000 });
    }).toPass({ timeout: 15_000 });
  }
);
