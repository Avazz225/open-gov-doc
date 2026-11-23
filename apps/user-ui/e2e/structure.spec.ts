import { test } from "@playwright/test";
import { expect, GATEWAY_BASE_URL, loginViaUi } from "./fixtures";

// P91-S3 (ADR 0255) against the running stack: a strict structure set up
// through the API (Abteilung at the root, below it only a "Team" from a
// template, Team admits only "Akte"), then used in the UI - "Neuer Ordner"
// points to the template, the template dialog creates the Team, the upload
// offers only Akte. Everything, including the installation's structure
// config, is restored in `finally`.
const SHOTS = process.env.E2E_SCREENSHOT_DIR;

async function adminHeaders(): Promise<Record<string, string>> {
  const login = await fetch(`${GATEWAY_BASE_URL}/api/auth-service/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: "config-admin", password: "config-admin" }),
  });
  const { access_token } = await login.json();
  return { Authorization: `Bearer ${access_token}`, "Content-Type": "application/json" };
}

async function api(headers: Record<string, string>, method: string, path: string, body?: unknown) {
  const response = await fetch(`${GATEWAY_BASE_URL}/api/${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status} ${await response.text()}`);
  return response.status === 204 ? null : response.json();
}

test("a strict structure guides new folders, templates and uploads", async ({ page }) => {
  const suffix = Date.now().toString(36);
  const names = { abteilung: `E2E-Abteilung-${suffix}`, team: `E2E-Team-${suffix}`, akte: `E2E-Akte-${suffix}` };
  const headers = await adminHeaders();
  const saved = await api(headers, "GET", "object-type-service/structure-config");
  const typeIds: number[] = [];
  let templateId: string | null = null;
  let rootFolderId: string | null = null;
  try {
    const akte = await api(headers, "POST", "object-type-service/object-types", {
      name: names.akte,
      applies_to: "document",
    });
    typeIds.push(akte.id);
    const team = await api(headers, "POST", "object-type-service/object-types", {
      name: names.team,
      applies_to: "folder",
      allowed_document_types: [names.akte],
      default_document_type: names.akte,
    });
    typeIds.push(team.id);
    const abteilung = await api(headers, "POST", "object-type-service/object-types", {
      name: names.abteilung,
      applies_to: "folder",
      allowed_parent_types: ["$ROOT"],
      allowed_child_folder_types: [names.team],
      children_from_template_only: true,
    });
    typeIds.push(abteilung.id);
    const template = await api(headers, "POST", "folder-service/folder-templates", {
      name: `Team-Gerüst ${suffix}`,
      structure: { name: "Team Hochbau", object_type_id: team.id, children: [] },
    });
    templateId = template.id;
    const folder = await api(headers, "POST", "folder-service/folders", {
      name: `Bauamt ${suffix}`,
      parent_id: "root",
      object_type_id: abteilung.id,
    });
    rootFolderId = folder.id;
    await api(headers, "PUT", "object-type-service/structure-config", { enforcement: "enforce" });

    await loginViaUi(page);
    await page.getByText(`Bauamt ${suffix}`).click({ timeout: 30_000 });

    // Below Abteilung, subfolders come only from a template.
    await page.getByRole("button", { name: "Neuer Ordner" }).click();
    const hint = page.getByRole("note").filter({ hasText: "nur aus Strukturvorlagen" });
    await expect(hint).toBeVisible();
    await hint.getByRole("button", { name: "Struktur aus Vorlage anlegen" }).click();
    const dialog = page.getByRole("dialog", { name: `Struktur aus Vorlage in „Bauamt ${suffix}“` });
    const offered = dialog.getByRole("button", { name: /^Vorlage „.*“ anlegen$/ });
    await expect(offered).toHaveCount(1);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/template-apply-1400.png` });
    await dialog.getByRole("button", { name: `Vorlage „Team-Gerüst ${suffix}“ anlegen` }).click();
    await expect(page.getByRole("status").filter({ hasText: "aus der Vorlage" })).toBeVisible();

    // In the Team, the upload offers only Akte, preselected.
    await page.getByText("Team Hochbau").click();
    await page.getByRole("button", { name: "Hochladen" }).first().click();
    const typeSelect = page.getByLabel("Objekttyp");
    await expect(typeSelect).toHaveValue(String(akte.id));
    await expect(typeSelect.locator("option")).toHaveText([names.akte]);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/upload-structure-1400.png` });
    // The narrow layout remounts the explorer, so the dialog is opened again.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "Hochladen" }).first().click();
    await expect(page.getByLabel("Objekttyp")).toHaveValue(String(akte.id));
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/upload-structure-390.png` });
  } finally {
    await api(headers, "PUT", "object-type-service/structure-config", {
      enforcement: saved.enforcement,
      root_allowed_folder_types: saved.root_allowed_folder_types,
      root_allowed_document_types: saved.root_allowed_document_types,
    });
    if (rootFolderId) {
      await fetch(`${GATEWAY_BASE_URL}/api/folder-service/folders/${rootFolderId}/trash`, {
        method: "POST",
        headers,
        body: JSON.stringify({}),
      });
    }
    if (templateId) await fetch(`${GATEWAY_BASE_URL}/api/folder-service/folder-templates/${templateId}`, { method: "DELETE", headers });
    for (const id of typeIds.reverse()) {
      await fetch(`${GATEWAY_BASE_URL}/api/object-type-service/object-types/${id}`, { method: "DELETE", headers });
    }
  }
});
