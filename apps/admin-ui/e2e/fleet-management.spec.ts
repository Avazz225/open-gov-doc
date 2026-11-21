import { test, expect, loginViaUi } from "./fixtures";

const FLEET_URL = process.env.E2E_FLEET_URL || "http://localhost:8031";

// Fleet management (P67-S1) - wires up fleet-management-service, which had
// no admin-ui caller at all before this session. Every fleet-management-
// service endpoint (including the plain list) requires the operator key
// (`DMS_FLEET_OPERATOR_KEY`) - not a normal admin-JWT-gated flow like the
// other E2E specs in this directory. P67-S1 itself set this key only
// TEMPORARILY for a one-off live verification, then removed it again
// (fail-closed default), which meant this very spec could never actually
// pass afterward - looked like "drifted UI text" in later gap-analysis
// rounds, but was really just a missing dev-only default. Phase 79 Session 3
// gave `infra/docker-compose.yml` a permanent `_dev_only`-style default for
// this key (same convention every other dev secret in that file already
// uses) - this constant's fallback must match it exactly.
const OPERATOR_KEY =
  process.env.E2E_FLEET_OPERATOR_KEY || "fleet-operator-dev-only-key";

test("registers a managed installation, shows the one-time key, then removes it", async ({
  page,
  request,
}) => {
  await loginViaUi(page);
  await page.goto("/fleet-management/");

  await page
    .getByPlaceholder("Erforderlich für jede Aktion auf dieser Seite")
    .fill(OPERATOR_KEY);
  await page.getByRole("button", { name: "Laden" }).click();

  await expect(page.getByText("Neue Installation registrieren")).toBeVisible({
    timeout: 15_000,
  });

  const displayName = `E2E-Installation-${Date.now()}`;
  try {
    await page.getByLabel("Anzeigename").fill(displayName);
    await page
      .getByLabel("Gateway-URL")
      .fill("https://e2e-test-installation.example.com");
    await page.getByRole("button", { name: "Registrieren" }).click();

    // The plaintext fleet-agent key is shown exactly once.
    await expect(page.locator("code")).toBeVisible({ timeout: 15_000 });
    const shownKey = await page.locator("code").textContent();
    expect(shownKey).toBeTruthy();
    expect(shownKey!.length).toBeGreaterThan(10);
    await page.getByRole("button", { name: "Verstanden" }).click();

    // Only the installations table (P88-S4b): since P88-S1 the groups table
    // lists every installation as an <option> in its add-member select, so a
    // page-wide `tr` filter matched two rows whenever a group existed - e.g.
    // while fleet-orchestration.spec.ts ran in parallel (the "warm-up flake"
    // noted in P88-S2c).
    const installations = page.locator("table.data-table").first();
    const row = installations.locator("tr", { hasText: displayName });
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(
      row.getByText("https://e2e-test-installation.example.com"),
    ).toBeVisible();

    await row.getByRole("button", { name: "Entfernen", exact: true }).click();
    await expect(
      installations.locator("tr", { hasText: displayName }),
    ).toHaveCount(0, {
      timeout: 15_000,
    });
  } finally {
    // A failure above must not leave the installation behind.
    const auth = { Authorization: `Bearer ${OPERATOR_KEY}` };
    const listed = await (
      await request.get(`${FLEET_URL}/installations`, { headers: auth })
    ).json();
    for (const installation of listed as {
      id: string;
      display_name: string;
    }[]) {
      if (installation.display_name === displayName) {
        await request.delete(`${FLEET_URL}/installations/${installation.id}`, {
          headers: auth,
        });
      }
    }
  }
});
