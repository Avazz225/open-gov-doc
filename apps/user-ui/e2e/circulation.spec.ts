import { expect, GATEWAY_BASE_URL, loginViaUi, TEST_PASSWORD, TEST_USERNAME } from "./fixtures";
import { test } from "@playwright/test";

// P93-S3 (ADR 0249 Part A b) against the running stack: a circulation folder
// from the shipped "Umlauf" template; mid-circulation a co-signer is added,
// another step struck, and the current step sent back to the author. The
// co-signers are the stack's technical accounts `config-admin` and
// `notification-admin`. Afterwards the folder is walked to its end through
// the API so it doesn't linger as open.
const SHOTS = process.env.E2E_SCREENSHOT_DIR;
const WORKFLOW = `${GATEWAY_BASE_URL}/api/workflow-service`;

async function apiToken(username: string): Promise<string> {
  const response = await fetch(`${GATEWAY_BASE_URL}/api/auth-service/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password: username === TEST_USERNAME ? TEST_PASSWORD : username }),
  });
  return ((await response.json()) as { access_token: string }).access_token;
}

test("circulation folder: add a co-signer, strike one, send back to the author", async ({ page }) => {
  test.setTimeout(120_000);
  const name = `E2E-Umlauf-${Date.now()}`;
  try {
    await loginViaUi(page);
    await page.getByRole("button", { name: "Umlaufmappen", exact: true }).click();
    await page.getByRole("button", { name: "Neue Umlaufmappe" }).click();
    const form = page.getByRole("form", { name: "Neue Umlaufmappe" });
    await form.getByLabel("Bezeichnung").fill(name);
    await expect(form.getByLabel("Ablauf")).toHaveValue(/\d+/);
    await expect(form.getByLabel("Ablauf").locator("option:checked")).toContainText("Umlauf");

    // The first route: config-admin co-signs, notification-admin signs.
    await form.getByLabel("Benutzername").fill("config-admin");
    await form.getByRole("button", { name: "Zum Laufweg hinzufügen" }).click();
    await expect(form.getByText("1. Mitzeichnen: config-admin")).toBeVisible();
    await form.getByLabel("Art").selectOption("zeichnen");
    await form.getByLabel("Benutzername").fill("notification-admin");
    await form.getByRole("button", { name: "Zum Laufweg hinzufügen" }).click();
    await form.getByRole("button", { name: "Umlaufmappe starten" }).click();

    await expect(page.getByRole("heading", { name })).toBeVisible({ timeout: 30_000 });
    const route = page.getByRole("region", { name: "Laufweg" });
    const steps = route.getByRole("listitem");
    await expect(steps).toHaveCount(2);
    await expect(steps.nth(0)).toContainText("Liegt gerade hier");

    // Add a co-signer right after the current step.
    await route.getByLabel("Begründung der Änderung").fill("Haushaltsmittel betroffen");
    const add = route.getByRole("form", { name: "Schritt hinzufügen" });
    await add.getByLabel("Art").selectOption("kenntnis");
    await add.getByLabel("Benutzername").fill(TEST_USERNAME);
    await add.getByLabel("Position").selectOption("next");
    await add.getByRole("button", { name: "Hinzufügen" }).click();
    await expect(route.getByRole("status")).toHaveText("Schritt hinzugefügt.");
    await expect(steps).toHaveCount(3);
    await expect(steps.nth(1)).toContainText("Zur Kenntnis");

    // Strike notification-admin.
    await route.getByLabel("Begründung der Änderung").fill("Zeichnung entfällt");
    await route.getByRole("button", { name: /notification-admin streichen/ }).click();
    await expect(route.getByRole("status")).toHaveText("Schritt gestrichen.");
    await expect(steps.nth(2)).toHaveAttribute("data-status", "removed");
    await expect(steps.nth(2)).toContainText("Gestrichen – Zeichnung entfällt");

    // Send the current step back to the author.
    await route.getByLabel("Begründung der Änderung").fill("Anlage fehlt");
    await route.getByRole("button", { name: "Zurückholen …" }).click();
    const recall = route.getByRole("form", { name: "Aktuellen Schritt zurückholen und umleiten" });
    await expect(recall.getByLabel("Umleiten an").locator("option:checked")).toContainText("Verfasser/in");
    await recall.getByLabel("Hinweis (optional)").fill("Bitte Anlage 3 ergänzen");
    await recall.getByRole("button", { name: "Zurückholen und umleiten" }).click();
    await expect(route.getByRole("status")).toHaveText("Schritt zurückgeholt und umgeleitet.");
    await expect(steps.nth(0)).toHaveAttribute("data-status", "recalled");
    await expect(steps.nth(1)).toHaveAttribute("data-status", "current");
    await expect(steps.nth(1)).toContainText("Bearbeiten");
    await expect(steps.nth(1)).toContainText("Bitte Anlage 3 ergänzen");

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
        await route.scrollIntoViewIfNeeded();
        await page.screenshot({ path: `${SHOTS}/user-circulation-route-${width}.png`, fullPage: true });
      }
    }

  } finally {
    // Walk the rest of the route as its assignees through the API.
    const token = await apiToken(TEST_USERNAME);
    const cases = (await (
      await fetch(`${GATEWAY_BASE_URL}/api/case-service/cases?status=open`, {
        headers: { Authorization: `Bearer ${token}` },
      })
    ).json()) as { name: string; process_instance_id: string | null }[];
    const instanceId = cases.find((c) => c.name === name)?.process_instance_id ?? null;
    for (let i = 0; instanceId && i < 5; i++) {
      const tasks = (await (
        await fetch(`${WORKFLOW}/instances/${instanceId}/tasks`, {
          headers: { Authorization: `Bearer ${token}` },
        })
      ).json()) as { id: string; circulation_step: { assignee: string } | null }[];
      if (!tasks.length) break;
      // Every remaining step belongs to users-admin (the author, the
      // "zur Kenntnis" step) unless the test stopped early.
      const response = await fetch(`${WORKFLOW}/instances/${instanceId}/tasks/${tasks[0].id}/complete`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: "{}",
      });
      if (!response.ok) break;
    }
  }
});
