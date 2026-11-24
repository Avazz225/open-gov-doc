import { expect, GATEWAY_BASE_URL, loginViaUi, test, TEST_PASSWORD, TEST_USERNAME } from "./fixtures";

// P92-S3 against the running stack: the signature page's four sections for
// incoming signatures (switches, EU trusted lists, installation anchors,
// backfill). Read-only: nothing is changed. `users-admin` gets
// `domain-admin-signature` for the test's duration.
const SHOTS = process.env.E2E_SCREENSHOT_DIR;

test("signature page: incoming-signature settings", async ({ page, request }) => {
  const login = await request.post(`${GATEWAY_BASE_URL}/api/auth-service/login`, {
    data: { username: TEST_USERNAME, password: TEST_PASSWORD },
  });
  const auth = { Authorization: `Bearer ${(await login.json()).access_token}` };
  const roles = await (await request.get(`${GATEWAY_BASE_URL}/api/permission-service/roles`, { headers: auth })).json();
  const roleId = roles.find((r: { name: string }) => r.name === "domain-admin-signature").id;
  const grant = await request.post(`${GATEWAY_BASE_URL}/api/permission-service/role-assignments`, {
    headers: auth,
    data: { principal_type: "user", principal_id: "2", role_id: roleId, resource_id: "root" },
  });
  const assignmentId = (await grant.json()).role_assignment.id;
  try {
    await loginViaUi(page);
    await page.goto("/signature-config/");
    await expect(page.getByRole("heading", { name: "Eingehende Signaturen prüfen" })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByLabel("Prüfung eingeschaltet")).toBeChecked();
    await expect(page.getByRole("heading", { name: "EU-Vertrauenslisten" })).toBeVisible();
    // The dev stack fetched the real lists in P92-S2.
    await expect(page.getByRole("cell", { name: "EU-Liste der Listen" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "DE", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Vertrauensanker der Installation" })).toBeVisible();
    await expect(page.getByText("Interne Signatur-CA (OG Doc)")).toBeVisible();
    const backfill = page.getByRole("form", { name: "Bestand nachprüfen" });
    await expect(backfill.getByRole("button", { name: "Nachprüfung starten" })).toBeDisabled();

    for (const [width, height] of [
      [1400, 900],
      [768, 1000],
      [390, 844],
    ] as const) {
      await page.setViewportSize({ width, height });
      await expect
        .poll(() => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth))
        .toBeLessThanOrEqual(0);
      if (SHOTS) {
        await page.getByRole("heading", { name: "Eingehende Signaturen prüfen" }).scrollIntoViewIfNeeded();
        await page.screenshot({ path: `${SHOTS}/admin-incoming-signatures-${width}.png`, fullPage: true });
      }
    }
  } finally {
    await request.delete(`${GATEWAY_BASE_URL}/api/permission-service/role-assignments/${assignmentId}`, {
      headers: auth,
    });
  }
});
