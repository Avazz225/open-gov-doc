import { test, expect, loginViaUi } from "./fixtures";

// Object-type editor CRUD (/object-types/) - second admin flow per the
// task (chosen over registry-overview/settings-toggle: it's the fuller
// create -> edit -> delete cycle, closer to "deep flow" than a read-only
// page). Confirmed against the running stack beforehand that `users-admin`
// can create/delete object types with its existing permissions - no new
// grant needed.
test("creates an object type, edits its attributes, and deletes it", async ({ page }) => {
  await loginViaUi(page);
  await page.goto("/object-types/");

  const typeName = `e2e-admin-ui-objtype-${Date.now()}`;

  const createForm = page.getByRole("form", { name: "Objekttyp anlegen" });
  await createForm.getByLabel("Name").fill(typeName);
  await createForm.getByRole("button", { name: "Attribut hinzufügen" }).click();
  await createForm.getByLabel("Technischer Name").fill("notiz");
  await createForm.getByRole("button", { name: "Anlegen" }).click();

  const typeRow = page.getByRole("row", { name: new RegExp(typeName) });
  await expect(typeRow).toBeVisible({ timeout: 15_000 });
  await expect(typeRow.getByRole("cell", { name: "1", exact: true })).toBeVisible();

  // Edit: add a second attribute and save.
  await typeRow.getByRole("button", { name: "Bearbeiten" }).click();
  const editForm = page.getByRole("form", { name: "Objekttyp speichern" });
  await expect(editForm).toBeVisible();
  await editForm.getByRole("button", { name: "Attribut hinzufügen" }).click();
  await editForm.getByLabel("Technischer Name").last().fill("notiz2");
  await editForm.getByRole("button", { name: "Speichern" }).click();

  await expect(typeRow.getByRole("cell", { name: "2", exact: true })).toBeVisible({
    timeout: 15_000,
  });

  // Delete.
  await typeRow.getByRole("button", { name: "Löschen" }).click();
  await expect(page.getByRole("row", { name: new RegExp(typeName) })).toHaveCount(0, {
    timeout: 15_000,
  });
});
