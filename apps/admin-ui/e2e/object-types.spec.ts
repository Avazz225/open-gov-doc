import { test, expect, loginViaUi } from "./fixtures";

// Object-type editor CRUD (/object-types/) - second admin flow per the
// task (chosen over registry-overview/settings-toggle: it's the fuller
// create -> edit -> delete cycle, closer to "deep flow" than a read-only
// page). Confirmed against the running stack beforehand that `users-admin`
// can create/delete object types with its existing permissions - no new
// grant needed.
test("creates an object type, edits its attributes, and deletes it", async ({ page }) => {
  // `/object-types/` is gated behind `admin.object_config` (RequireCapability)
  // - `users-admin` only carries `admin.user_management` and does not have
  // this capability (confirmed live against the running stack, Phase 79
  // Session 3), so this spec must log in as `config-admin` instead. This,
  // not stale UI text, was the actual reason this spec previously timed out.
  await loginViaUi(page, { username: "config-admin", password: "config-admin" });
  await page.goto("/object-types/");

  const typeName = `e2e-admin-ui-objtype-${Date.now()}`;

  const createForm = page.getByRole("form", { name: "Objekttyp anlegen" });
  await createForm.getByLabel("Name", { exact: true }).fill(typeName);
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

// P88-S3: the guided rule editors. A naming rule, a condition and a status
// transition are saved, survive a second save (which used to wipe the
// transitions) and are read back from the server; the type is deleted
// afterwards.
test("edits naming rule, condition and status transition and keeps them across saves", async ({
  page,
}) => {
  await loginViaUi(page, { username: "config-admin", password: "config-admin" });
  await page.goto("/object-types/");
  const typeName = `e2e-rules-${Date.now()}`;

  const createForm = page.getByRole("form", { name: "Objekttyp anlegen" });
  await createForm.getByLabel("Name", { exact: true }).fill(typeName);
  for (const attribute of ["Rechnungsnummer", "Betrag", "Kostenstelle"]) {
    await createForm.getByRole("button", { name: "Attribut hinzufügen" }).click();
    await createForm.getByLabel("Technischer Name").last().fill(attribute);
  }
  // Naming rule.
  await createForm.getByLabel("Namensmuster").fill("RE-");
  await createForm.getByRole("button", { name: "Platzhalter Rechnungsnummer einfügen" }).click();
  await createForm.getByLabel("Beispielname prüfen").fill("RE-4711.pdf");
  await expect(createForm.getByRole("status")).toHaveText("Passt zum Muster");
  // Condition.
  await createForm.getByRole("button", { name: "Bedingung hinzufügen" }).click();
  const condition = createForm.getByRole("listitem", { name: "Bedingung 1" });
  await condition.getByRole("combobox", { name: /^Attribut/ }).selectOption("Betrag");
  await condition.getByRole("combobox", { name: /^Vergleich/ }).selectOption(">");
  await condition.getByLabel("Wert").fill("10000");
  await condition.getByRole("combobox", { name: /^Pflichtattribut/ }).selectOption("Kostenstelle");
  // Status transition.
  await createForm.getByRole("button", { name: "Statusübergang hinzufügen" }).click();
  const transition = createForm.getByRole("listitem", { name: "Statusübergang 1" });
  await transition.getByLabel("Von Status").fill("open");
  await transition.getByLabel("Nach Status").fill("closed");
  await transition.getByLabel("Kostenstelle").check();
  await createForm.getByRole("button", { name: "Anlegen" }).click();

  const typeRow = page.getByRole("row", { name: new RegExp(typeName) });
  await expect(typeRow).toBeVisible({ timeout: 15_000 });

  // Open, save unchanged, open again: everything is still there.
  for (let round = 0; round < 2; round++) {
    await typeRow.getByRole("button", { name: "Bearbeiten" }).click();
    const editForm = page.getByRole("form", { name: "Objekttyp speichern" });
    await expect(editForm.getByLabel("Namensmuster")).toHaveValue("RE-{Rechnungsnummer}");
    const savedCondition = editForm.getByRole("listitem", { name: "Bedingung 1" });
    await expect(savedCondition.getByRole("combobox", { name: /^Attribut/ })).toHaveValue("Betrag");
    await expect(savedCondition.getByLabel("Wert")).toHaveValue("10000");
    const savedTransition = editForm.getByRole("listitem", { name: "Statusübergang 1" });
    await expect(savedTransition.getByLabel("Nach Status")).toHaveValue("closed");
    await expect(savedTransition.getByLabel("Kostenstelle")).toBeChecked();
    await editForm.getByRole("button", { name: "Speichern" }).click();
    await expect(editForm).toHaveCount(0, { timeout: 15_000 });
  }

  await typeRow.getByRole("button", { name: "Löschen" }).click();
  await expect(page.getByRole("row", { name: new RegExp(typeName) })).toHaveCount(0, {
    timeout: 15_000,
  });
});
