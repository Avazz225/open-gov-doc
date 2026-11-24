import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, GATEWAY_BASE_URL, loginViaUi, testWithIsolatedFolder } from "./fixtures";

// P92-S3 (ADR 0248) against the running stack: two signed PDFs - one valid
// against an installation trust anchor, one changed after signing - show
// their badges in the list, and the document's "Eingegangene Signaturen"
// section lists signer, trust and coverage, downloads the report and checks
// again. `users-admin` gets `domain-admin-signature` for the test's duration
// (to add the anchor); anchor and grant are removed afterwards.
const SHOTS = process.env.E2E_SCREENSHOT_DIR;
const FIXTURES = path.join(__dirname, "fixtures");
const API = `${GATEWAY_BASE_URL}/api`;

async function api(token: string, method: string, url: string, body?: unknown) {
  return fetch(`${API}/${url}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function upload(token: string, folderId: string, file: string): Promise<string> {
  const form = new FormData();
  form.append("title", file);
  form.append("folder_id", folderId);
  form.append(
    "file",
    new Blob([readFileSync(path.join(FIXTURES, file))], { type: "application/pdf" }),
    file
  );
  const response = await fetch(`${API}/document-service/documents`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  expect(response.ok).toBe(true);
  return ((await response.json()) as { id: string }).id;
}

async function waitForResult(token: string, documentIds: string[]) {
  for (let i = 0; i < 60; i++) {
    const response = await api(
      token,
      "GET",
      `signature-service/incoming-signatures/summaries?document_ids=${documentIds.join(",")}`
    );
    const body = (await response.json()) as Record<string, unknown[]>;
    if (documentIds.every((id) => body[id]?.length)) return body;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("no validation results");
}

testWithIsolatedFolder(
  "shows incoming signatures in the list and the document",
  async ({ page, isolatedFolder }) => {
    testWithIsolatedFolder.setTimeout(90_000);
    const token = isolatedFolder.token;
    const roles = (await (await api(token, "GET", "permission-service/roles")).json()) as {
      id: number;
      name: string;
    }[];
    const roleId = roles.find((r) => r.name === "domain-admin-signature")!.id;
    const grant = await api(token, "POST", "permission-service/role-assignments", {
      principal_type: "user",
      principal_id: "2",
      role_id: roleId,
      resource_id: "root",
    });
    const assignmentId = ((await grant.json()) as { role_assignment: { id: number } })
      .role_assignment.id;
    let anchorId: number | null = null;
    try {
      const pem = readFileSync(path.join(FIXTURES, "partner-root.pem"), "utf8");
      const added = await api(token, "POST", "signature-service/incoming-signatures/trust-anchors", {
        label: "E2E Partnerbehörde",
        certificate_pem: pem,
      });
      if (added.status === 201) {
        anchorId = ((await added.json()) as { id: number }).id;
      } else {
        // Left over from an aborted run: reuse it and remove it at the end.
        const anchors = (await (
          await api(token, "GET", "signature-service/incoming-signatures/trust-anchors")
        ).json()) as { id: number | null; label: string }[];
        anchorId = anchors.find((a) => a.label === "E2E Partnerbehörde")?.id ?? null;
      }

      const valid = await upload(token, isolatedFolder.id, "signed-valid.pdf");
      const changed = await upload(token, isolatedFolder.id, "signed-changed.pdf");
      await waitForResult(token, [valid, changed]);

      await loginViaUi(page);
      await page.getByText(isolatedFolder.name).click({ timeout: 30_000 });
      const validRow = page.locator(".entry-row", { hasText: "signed-valid.pdf" });
      const changedRow = page.locator(".entry-row", { hasText: "signed-changed.pdf" });
      await expect(validRow.getByTestId("signature-badge")).toHaveText("Gültig signiert");
      await expect(changedRow.getByTestId("signature-badge")).toHaveText("Signatur ungültig");
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/signature-badges-1400.png` });

      await validRow.getByRole("button", { name: /signed-valid\.pdf/ }).click();
      const section = page.locator("section.incoming-signatures").first();
      await expect(section.getByText("Erika Mustermann (E2E)")).toBeVisible({ timeout: 30_000 });
      await expect(section.getByText("vertrauenswürdig (Installation)")).toBeVisible();
      await expect(section.getByText("deckt das ganze Dokument ab")).toBeVisible();
      await section.scrollIntoViewIfNeeded();
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/incoming-signatures-1400.png` });

      // The preview's actions as one toolbar with a "Weitere Aktionen" menu.
      const toolbar = page.getByRole("toolbar", { name: /Aktionen für/ });
      await expect(toolbar.getByRole("button", { name: "Herunterladen" })).toBeVisible();
      await toolbar.getByRole("button", { name: /Weitere Aktionen/ }).click();
      await expect(page.getByRole("menuitem", { name: /^Export für Behördenübergabe/ })).toBeVisible();
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/preview-actions-1400.png` });
      await page.keyboard.press("Escape");

      const download = page.waitForEvent("download");
      await section.getByRole("button", { name: "Prüfbericht herunterladen" }).click();
      expect((await download).suggestedFilename()).toMatch(/^pruefbericht-.*\.xml$/);

      await section.getByRole("button", { name: "Erneut prüfen" }).click();
      await expect(section.getByRole("button", { name: "Erneut prüfen" })).toBeEnabled({
        timeout: 30_000,
      });

      for (const [width, height] of [
        [768, 1000],
        [390, 844],
      ] as const) {
        await page.setViewportSize({ width, height });
        // The layout settles after the resize (single-pane switch below 768px).
        await expect
          .poll(() => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth))
          .toBeLessThanOrEqual(0);
        if (width < 768) {
          // Single-pane mode: switch to the metadata pane to see the section.
          await page.getByRole("button", { name: "Metadaten" }).click();
          await expect(page.locator("section.incoming-signatures").first()).toBeVisible();
          await page.locator("section.incoming-signatures").first().scrollIntoViewIfNeeded();
        }
        if (SHOTS) await page.screenshot({ path: `${SHOTS}/incoming-signatures-${width}.png` });
      }
    } finally {
      if (anchorId !== null) {
        await api(token, "DELETE", `signature-service/incoming-signatures/trust-anchors/${anchorId}`);
      }
      await api(token, "DELETE", `permission-service/role-assignments/${assignmentId}`);
    }
  }
);

// P92-S4: a text file uploaded with its detached `.p7s` through the upload
// form, and an enveloping `.p7m` whose content becomes a derived document.
testWithIsolatedFolder(
  "CAdES: a .p7s from the upload form and a .p7m with its extracted content",
  async ({ page, isolatedFolder }) => {
    testWithIsolatedFolder.setTimeout(120_000);
    const token = isolatedFolder.token;
    const roles = (await (await api(token, "GET", "permission-service/roles")).json()) as {
      id: number;
      name: string;
    }[];
    const roleId = roles.find((r) => r.name === "domain-admin-signature")!.id;
    const grant = await api(token, "POST", "permission-service/role-assignments", {
      principal_type: "user",
      principal_id: "2",
      role_id: roleId,
      resource_id: "root",
    });
    const assignmentId = ((await grant.json()) as { role_assignment: { id: number } })
      .role_assignment.id;
    let anchorId: number | null = null;
    try {
      const added = await api(token, "POST", "signature-service/incoming-signatures/trust-anchors", {
        label: "E2E CAdES-Partner",
        certificate_pem: readFileSync(path.join(FIXTURES, "cades-root.pem"), "utf8"),
      });
      anchorId =
        added.status === 201
          ? ((await added.json()) as { id: number }).id
          : ((
              (await (
                await api(token, "GET", "signature-service/incoming-signatures/trust-anchors")
              ).json()) as { id: number | null; label: string }[]
            ).find((a) => a.label === "E2E CAdES-Partner")?.id ?? null);

      await loginViaUi(page);
      await page.getByText(isolatedFolder.name).click({ timeout: 30_000 });
      await page.getByRole("button", { name: "Hochladen" }).click();
      const form = page.getByRole("form", { name: /Dokument hochladen/ });
      await form.getByLabel("Datei", { exact: true }).setInputFiles(path.join(FIXTURES, "bescheid.txt"));
      await form.getByRole("button", { name: /Abgesetzte Signatur/ }).click();
      await form
        .getByLabel("Abgesetzte Signatur (.p7s)")
        .setInputFiles(path.join(FIXTURES, "bescheid.txt.p7s"));
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/upload-with-signature-1400.png` });
      await form.getByRole("button", { name: "Hochladen" }).click();
      await expect(form).toHaveCount(0, { timeout: 30_000 });

      // The p7m goes in through the API.
      const p7mForm = new FormData();
      p7mForm.append("title", "bescheid.txt.p7m");
      p7mForm.append("folder_id", isolatedFolder.id);
      p7mForm.append(
        "file",
        new Blob([readFileSync(path.join(FIXTURES, "bescheid.txt.p7m"))]),
        "bescheid.txt.p7m"
      );
      const p7m = await fetch(`${API}/document-service/documents`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: p7mForm,
      });
      expect(p7m.ok).toBe(true);
      const p7mId = ((await p7m.json()) as { id: string }).id;
      // Wait for the derived content document.
      await expect
        .poll(
          async () =>
            ((await (await api(token, "GET", `document-service/documents/${p7mId}/derived`)).json()) as
              unknown[]).length,
          { timeout: 30_000 }
        )
        .toBe(1);

      // Wait until the uploaded text file (via its .p7s) and the .p7m have
      // results, then reload the listing once.
      const listing = (await (
        await api(token, "GET", `document-service/documents?folder_id=${isolatedFolder.id}`)
      ).json()) as { id: string; title: string; derivation_type: string | null }[];
      // Not the copy extracted from the .p7m (derivation "signed_content").
      const uploaded = listing.find((d) => d.title === "bescheid.txt" && !d.derivation_type)!;
      await waitForResult(token, [uploaded.id, p7mId]);
      await page.reload();
      await page.getByText(isolatedFolder.name).click({ timeout: 30_000 });
      await expect(page.getByTestId("signature-badge")).toHaveCount(2, { timeout: 15_000 });
      // The content extracted from the .p7m, told apart by its title.
      await expect(
        page.locator(".entry-row", { hasText: "bescheid.txt (Inhalt von bescheid.txt.p7m)" })
      ).toHaveCount(1);
      const original = page.locator(".entry-row", { hasText: "bescheid.txt" }).filter({
        hasNot: page.getByText(/\.p7[ms]|Inhalt von/),
      });
      await expect(original).toHaveCount(1);
      await expect(
        page.locator(".entry-row", { hasText: "bescheid.txt.p7m" }).getByTestId("signature-badge")
      ).toHaveText("Gültig signiert");
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/cades-list-1400.png` });

      // The uploaded text file shows its detached signature.
      await expect(original.getByTestId("signature-badge")).toHaveText("Gültig signiert");
      await original.getByRole("button", { name: "bescheid.txt" }).click();
      const section = page.locator("section.incoming-signatures").first();
      await expect(section.getByText("Signaturdatei: bescheid.txt.p7s")).toBeVisible({
        timeout: 30_000,
      });
      await expect(section.getByText("Erika Mustermann (E2E CAdES)")).toBeVisible();
      await section.scrollIntoViewIfNeeded();
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/cades-section-1400.png` });
    } finally {
      if (anchorId !== null) {
        await api(token, "DELETE", `signature-service/incoming-signatures/trust-anchors/${anchorId}`);
      }
      await api(token, "DELETE", `permission-service/role-assignments/${assignmentId}`);
    }
  }
);
