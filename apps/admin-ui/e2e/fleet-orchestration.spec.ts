import { test, expect, loginViaUi } from "./fixtures";

// Fleet orchestration (P88-S1): a group, a one-step plan and a rollout from
// that group, run to completion through the UI, then everything removed
// again in dependency order (rollout, plan, group, installation) so the dev
// stack's fleet tables stay empty.
const OPERATOR_KEY = process.env.E2E_FLEET_OPERATOR_KEY || "fleet-operator-dev-only-key";

test("runs a rollout from a group to completion and cleans up after itself", async ({ page }) => {
  page.on("dialog", (dialog) => dialog.accept());
  await loginViaUi(page);
  await page.goto("/fleet-management/");
  await page.getByPlaceholder("Erforderlich für jede Aktion auf dieser Seite").fill(OPERATOR_KEY);
  await page.getByRole("button", { name: "Laden" }).click();
  await expect(page.getByText("Neue Installation registrieren")).toBeVisible({ timeout: 15_000 });

  const suffix = Date.now();
  const installationName = `E2E-Rollout-${suffix}`;
  await page.getByLabel("Anzeigename").fill(installationName);
  await page.getByLabel("Gateway-URL").fill("https://e2e-rollout.example.com");
  await page.getByRole("button", { name: "Registrieren" }).click();
  await page.getByRole("button", { name: "Verstanden" }).click();

  // Group with the installation as its member.
  const groups = page.getByRole("region", { name: "Gruppen (Wellen)" });
  const groupName = `E2E-Welle-${suffix}`;
  await groups.getByLabel("Name").fill(groupName);
  await groups.getByRole("button", { name: "Gruppe anlegen" }).click();
  const groupRow = groups.locator("tr", { hasText: groupName });
  await groupRow.getByRole("combobox").selectOption({ label: installationName });
  await groupRow.getByRole("button", { name: "Hinzufügen" }).click();
  await expect(groupRow.getByText(installationName)).toBeVisible({ timeout: 15_000 });

  // One confirmation step.
  const planForm = page.getByRole("form", { name: "Neuer Update-Plan" });
  const planName = `E2E-Plan-${suffix}`;
  await planForm.getByLabel("Name").fill(planName);
  await planForm.getByLabel("Version").fill("1.0");
  await planForm.getByLabel("Schritt", { exact: true }).fill("Update eingespielt");
  await planForm.getByRole("button", { name: "Plan anlegen" }).click();
  await expect(page.getByText(planName).first()).toBeVisible({ timeout: 15_000 });

  // Rollout from the group, started and reported.
  const rolloutForm = page.getByRole("form", { name: "Neuer Rollout" });
  const rolloutName = `E2E-Rollout-${suffix}`;
  await rolloutForm.getByLabel("Name").fill(rolloutName);
  await rolloutForm.getByLabel("Plan").selectOption({ label: `${planName} v1.0` });
  await rolloutForm.getByLabel("Gruppe").selectOption({ label: groupName });
  await expect(rolloutForm.getByText("Zielkreis: 1 Installation(en)")).toBeVisible();
  await rolloutForm.getByRole("button", { name: "Rollout anlegen" }).click();

  const rollout = page.getByRole("listitem", { name: rolloutName });
  await expect(rollout.getByText("Entwurf")).toBeVisible({ timeout: 15_000 });
  await rollout.getByRole("button", { name: "Starten" }).click();
  await expect(rollout.getByText("Wartet auf Schritt")).toBeVisible({ timeout: 15_000 });
  await expect(rollout.getByText(/gestartet von bootstrap/)).toBeVisible();

  await rollout.getByRole("button", { name: "Ergebnis melden" }).click();
  await rollout.getByRole("button", { name: "Melden" }).click();
  await expect(rollout.locator(".badge", { hasText: "Abgeschlossen" })).toHaveCount(2, { timeout: 15_000 });

  // Cleanup in dependency order.
  await rollout.getByRole("button", { name: "Entfernen", exact: true }).click();
  await expect(page.getByRole("listitem", { name: rolloutName })).toHaveCount(0, { timeout: 15_000 });
  await page.locator("li", { hasText: planName }).getByRole("button", { name: "Entfernen", exact: true }).click();
  await expect(page.locator("li", { hasText: planName })).toHaveCount(0, { timeout: 15_000 });
  await groups.locator("tr", { hasText: groupName }).getByRole("button", { name: "Entfernen", exact: true }).click();
  await expect(groups.locator("tr", { hasText: groupName })).toHaveCount(0, { timeout: 15_000 });
  await page.locator("tr", { hasText: installationName }).getByRole("button", { name: "Entfernen", exact: true }).click();
  await expect(page.locator("tr", { hasText: installationName })).toHaveCount(0, { timeout: 15_000 });
});
