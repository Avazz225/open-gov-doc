import { test, expect, loginViaUi, GATEWAY_BASE_URL } from "./fixtures";

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

// P90-S5 (ADR 0258): embedded-metadata mappings. A document type maps the
// PDF title and creation date; a real upload through the gateway then shows
// both in the new document's attributes. The document and type are removed
// afterwards.
function pdfWithInfo(title: string, creationDate: string): Buffer {
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] >>",
    `<< /Title (${title}) /CreationDate (${creationDate}) >>`,
  ];
  let body = "%PDF-1.4\n";
  const offsets: number[] = [];
  objects.forEach((content, index) => {
    offsets.push(Buffer.byteLength(body));
    body += `${index + 1} 0 obj\n${content}\nendobj\n`;
  });
  const xrefAt = Buffer.byteLength(body);
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  body += offsets.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("");
  body += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R /Info 4 0 R >>\nstartxref\n${xrefAt}\n%%EOF\n`;
  return Buffer.from(body, "latin1");
}

test("maps PDF properties to attributes and fills them on upload", async ({ page, request }) => {
  const shots = process.env.E2E_SCREENSHOT_DIR;
  await loginViaUi(page, { username: "config-admin", password: "config-admin" });
  await page.goto("/object-types/");
  const typeName = `e2e-mappings-${Date.now()}`;

  const createForm = page.getByRole("form", { name: "Objekttyp anlegen" });
  await createForm.getByLabel("Name", { exact: true }).fill(typeName);
  for (const [attribute, type] of [
    ["Betreff", "string"],
    ["Belegdatum", "date"],
  ] as const) {
    await createForm.getByRole("button", { name: "Attribut hinzufügen" }).click();
    await createForm.getByLabel("Technischer Name").last().fill(attribute);
    // A wrapping <label> also names its select by the chosen option, hence the prefix match.
    await createForm.getByRole("combobox", { name: /^Typ/ }).last().selectOption(type);
  }
  await createForm.getByRole("button", { name: "Zuordnung hinzufügen" }).click();
  const first = createForm.getByRole("listitem", { name: "Zuordnung 1" });
  await first.getByLabel("Attribut").selectOption("Betreff");
  await createForm.getByRole("button", { name: "Zuordnung hinzufügen" }).click();
  const second = createForm.getByRole("listitem", { name: "Zuordnung 2" });
  await second.getByLabel("Dateieigenschaft").selectOption("created");
  await second.getByLabel("Attribut").selectOption("Belegdatum");
  await second.getByLabel("Übernahme").selectOption("always");
  const mappings = createForm.getByRole("group", { name: "Metadaten aus Dateien übernehmen" });
  for (const [width, height] of [
    [1400, 900],
    [390, 844],
  ] as const) {
    await page.setViewportSize({ width, height });
    await mappings.scrollIntoViewIfNeeded();
    if (shots) await mappings.screenshot({ path: `${shots}/metadata-mappings-${width}.png` });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  }
  await page.setViewportSize({ width: 1400, height: 900 });
  await createForm.getByRole("button", { name: "Anlegen" }).click();
  const typeRow = page.getByRole("row", { name: new RegExp(typeName) });
  await expect(typeRow).toBeVisible({ timeout: 15_000 });

  const login = await request.post(`${GATEWAY_BASE_URL}/api/auth-service/login`, {
    data: { username: "config-admin", password: "config-admin" },
  });
  const auth = { Authorization: `Bearer ${(await login.json()).access_token}` };
  const types = await (await request.get(`${GATEWAY_BASE_URL}/api/object-type-service/object-types`, { headers: auth })).json();
  const created = types.find((t: { name: string }) => t.name === typeName);
  let documentId: string | null = null;
  try {
    expect(created.metadata_mappings).toEqual([
      { source: "title", attribute: "Betreff", overwrite: "if_empty" },
      { source: "created", attribute: "Belegdatum", overwrite: "always" },
    ]);
    const upload = await request.post(`${GATEWAY_BASE_URL}/api/document-service/documents`, {
      headers: auth,
      multipart: {
        title: "Bescheid",
        object_type_id: String(created.id),
        attributes: JSON.stringify({ Belegdatum: "2020-01-01" }),
        file: { name: "bescheid.pdf", mimeType: "application/pdf", buffer: pdfWithInfo("Bescheid aus Datei", "D:20240131093000") },
      },
    });
    expect(upload.status(), await upload.text()).toBe(201);
    const document = await upload.json();
    documentId = document.id;
    // `if_empty` filled the empty subject; `always` replaced the typed date.
    expect(document.attributes).toEqual({ Betreff: "Bescheid aus Datei", Belegdatum: "2024-01-31" });
  } finally {
    if (documentId) {
      await request.delete(`${GATEWAY_BASE_URL}/api/document-service/documents/${documentId}`, { headers: auth });
    }
    await typeRow.getByRole("button", { name: "Löschen" }).click();
    await expect(page.getByRole("row", { name: new RegExp(typeName) })).toHaveCount(0, { timeout: 15_000 });
  }
});
