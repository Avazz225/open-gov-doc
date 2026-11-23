import { expect, GATEWAY_BASE_URL, loginViaUi, testWithIsolatedFolder } from "./fixtures";

// P91-S4 (ADR 0259) against the running stack: a subfolder isolated from the
// default grant (as a teamspace root is) no longer shows up for someone who
// can't open it, and "Hochladen in „…“" from a folder's context menu files
// the document into that folder, not the one being shown.
const PERMISSION_SERVICE_URL = process.env.E2E_PERMISSION_SERVICE_URL || "http://localhost:8004";
const SHOTS = process.env.E2E_SCREENSHOT_DIR;

async function createFolder(token: string, name: string, parentId: string): Promise<string> {
  const response = await fetch(`${GATEWAY_BASE_URL}/api/folder-service/folders`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ name, parent_id: parentId }),
  });
  const { id } = (await response.json()) as { id: string };
  // The resource node arrives via `folder.resource.created`.
  for (let i = 0; i < 50; i++) {
    if ((await fetch(`${PERMISSION_SERVICE_URL}/resources/${id}`)).ok) return id;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`resource node for ${id} never appeared`);
}

testWithIsolatedFolder("hides what can't be read and uploads by right-click", async ({ page, isolatedFolder }) => {
  const visible = await createFolder(isolatedFolder.token, "Sichtbar", isolatedFolder.id);
  const hidden = await createFolder(isolatedFolder.token, "Verborgen", isolatedFolder.id);
  const isolate = await fetch(`${PERMISSION_SERVICE_URL}/resources/${hidden}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ inherit: false }),
  });
  expect(isolate.ok).toBe(true);

  await loginViaUi(page);
  await page.getByText(isolatedFolder.name).click({ timeout: 30_000 });
  await expect(page.getByText("Sichtbar")).toBeVisible();
  await expect(page.getByText("Verborgen")).toHaveCount(0);

  await page.getByText("Sichtbar").click({ button: "right" });
  const item = page.getByRole("menuitem", { name: 'Hochladen in "Sichtbar" …' });
  await expect(item).toBeVisible();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/context-upload-1400.png` });
  await item.click();
  const dialog = page.getByRole("dialog", { name: "Dokument hochladen in „Sichtbar“" });
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "rechtsklick.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("per Rechtsklick hochgeladen"),
  });
  await dialog.getByRole("button", { name: "Hochladen" }).click();
  await expect(dialog).toHaveCount(0);

  // Not in the folder being shown, but in "Sichtbar".
  await expect(page.getByText("rechtsklick.txt")).toHaveCount(0);
  await page.getByText("Sichtbar").click();
  await expect(page.getByText("rechtsklick.txt")).toBeVisible();
  void visible;

  for (const [width, height] of [
    [768, 1000],
    [390, 844],
  ] as const) {
    await page.setViewportSize({ width, height });
    await page.getByText("rechtsklick.txt").click({ button: "right" });
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: isolatedFolder.name }).first().click();
    await page.getByText("Sichtbar").click({ button: "right" });
    await expect(page.getByRole("menuitem", { name: 'Hochladen in "Sichtbar" …' })).toBeVisible();
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/context-upload-${width}.png` });
    await page.keyboard.press("Escape");
    await page.getByText("Sichtbar").click();
  }
});
